"""Star Citizen game-telemetry stream: protobuf decode, schema from the exe, gRPC client.

Stdlib only. The game serves sc.game.telemetry.v1.GameTelemetryService on 127.0.0.1:2942
(cleartext HTTP/2) when user.cfg has `grpc.client.server.enabled = true`.

Field names come from the FileDescriptorProtos compiled into StarCitizen.exe, so a patch
that adds or renumbers fields needs no code change here.
"""
import hashlib
import json
import mmap
import os
import re
import socket
import struct
import sys

PKG = "sc.game.telemetry.v1"
STREAM_PATH = f"/{PKG}.GameTelemetryService/TelemetryStream"
RESP_TYPE = PKG + ".TelemetryStreamResponse"
HOST = "127.0.0.1"
PORT = 2942


# --- protobuf wire format ---------------------------------------------------------

def _varint(b, i):
    r = s = 0
    while True:
        c = b[i]
        i += 1
        r |= (c & 0x7F) << s
        s += 7
        if c < 0x80:
            return r, i


def wire_fields(b):
    """(field_no, wire_type, value) for each field in a serialized message."""
    i, n = 0, len(b)
    while i < n:
        key, i = _varint(b, i)
        f, w = key >> 3, key & 7
        if w == 0:
            v, i = _varint(b, i)
        elif w == 1:
            v, i = b[i:i + 8], i + 8
        elif w == 2:
            ln, i = _varint(b, i)
            v, i = b[i:i + ln], i + ln
        elif w == 5:
            v, i = b[i:i + 4], i + 4
        else:
            raise ValueError(f"wire type {w}")
        yield f, w, v


# --- schema from the exe ----------------------------------------------------------

T_DOUBLE, T_FLOAT, T_INT64, T_UINT64, T_INT32 = 1, 2, 3, 4, 5
T_BOOL, T_STRING, T_MESSAGE, T_BYTES, T_UINT32, T_ENUM = 8, 9, 11, 12, 13, 14
T_SINT32, T_SINT64 = 17, 18
TYPE_NAMES = {1: "double", 2: "float", 3: "int64", 4: "uint64", 5: "int32", 8: "bool",
              9: "string", 11: "msg", 12: "bytes", 13: "uint32", 14: "enum",
              17: "sint32", 18: "sint64"}


def find_exe():
    for drive in ("C:", "D:", "E:", "F:"):
        for chan in ("LIVE", "PTU", "TECH-PREVIEW"):
            p = rf"{drive}\Program Files\Roberts Space Industries\StarCitizen\{chan}\Bin64\StarCitizen.exe"
            if os.path.isfile(p):
                return p
    return None


def descriptor_blobs(exe_path, match=b"telemetry"):
    """Serialized FileDescriptorProtos in the exe whose file name contains `match`."""
    with open(exe_path, "rb") as fh:
        m = mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ)
        out = []
        for x in re.finditer(rb"\x0a([\x05-\x7f])([ -~]*" + match + rb"[ -~]*\.proto)", m):
            if len(x.group(2)) != x.group(1)[0]:
                continue
            b = bytes(m[x.start():x.start() + 200000])
            i = 0
            try:   # walk top-level fields until one is not a plausible FileDescriptorProto field
                while i < len(b):
                    key, j = _varint(b, i)
                    f, w = key >> 3, key & 7
                    if f == 0 or f > 12 or w not in (0, 2):
                        break
                    if w == 0:
                        _, j = _varint(b, j)
                    else:
                        ln, j = _varint(b, j)
                        j += ln
                    if j > len(b):
                        break
                    i = j
            except IndexError:
                pass
            out.append((x.group(2).decode(), b[:i]))
        return out


class Schema:
    def __init__(self):
        self.messages = {}   # full name -> {field_no: (name, type, type_name, repeated)}
        self.enums = {}      # full name -> {number: short value name}
        self.services = {}   # full name -> [(rpc, in, out, server_streaming)]
        self.files = []
        self.hash = ""

    @classmethod
    def from_blobs(cls, blobs):
        s = cls()
        sha = hashlib.sha1()
        for fname, blob in blobs:
            s.files.append(fname)
            s._file(blob)
            sha.update(blob)
        s.hash = sha.hexdigest()[:12]
        return s

    @classmethod
    def from_exe(cls, exe_path):
        return cls.from_blobs(descriptor_blobs(exe_path))

    def to_json(self):
        return {
            "hash": self.hash,
            "files": list(self.files),
            "messages": {n: {str(no): list(spec) for no, spec in t.items()} for n, t in self.messages.items()},
            "enums": {n: {str(k): v for k, v in e.items()} for n, e in self.enums.items()},
            "services": {n: [list(r) for r in rpcs] for n, rpcs in self.services.items()},
        }

    @classmethod
    def from_json(cls, d):
        s = cls()
        s.hash = d.get("hash", "")
        s.files = list(d.get("files", []))
        s.messages = {n: {int(no): tuple(spec) for no, spec in t.items()} for n, t in d["messages"].items()}
        s.enums = {n: {int(k): v for k, v in e.items()} for n, e in d["enums"].items()}
        s.services = {n: [tuple(r) for r in rpcs] for n, rpcs in d["services"].items()}
        return s

    def _file(self, b):
        fields = list(wire_fields(b))
        pkg = next((v.decode() for f, _, v in fields if f == 2), "")
        for f, _, v in fields:
            if f == 4:
                self._message(v, pkg)
            elif f == 5:
                self._enum(v, pkg)
            elif f == 6:
                self._service(v, pkg)

    def _message(self, b, scope):
        fields = list(wire_fields(b))
        name = scope + "." + next(v.decode() for f, _, v in fields if f == 1)
        table = self.messages.setdefault(name, {})
        for f, _, v in fields:
            if f == 2:
                d = {ff: vv for ff, _, vv in wire_fields(v)}
                table[d[3]] = (d[1].decode(), d.get(5), d.get(6, b"").decode().lstrip("."),
                               d.get(4) == 3)
            elif f == 3:
                self._message(v, name)
            elif f == 4:
                self._enum(v, name)

    def _enum(self, b, scope):
        fields = list(wire_fields(b))
        name = scope + "." + next(v.decode() for f, _, v in fields if f == 1)
        vals = {}
        for f, _, v in fields:
            if f == 2:
                d = {ff: vv for ff, _, vv in wire_fields(v)}
                vals[d.get(2, 0)] = d[1].decode()
        # ANNUNCIATOR_STATE_MAJOR_ON -> MAJOR_ON
        prefix = os.path.commonprefix([n for n in vals.values()])
        prefix = prefix[:prefix.rfind("_") + 1] if len(vals) > 1 else ""
        self.enums[name] = {k: v[len(prefix):] for k, v in vals.items()}

    def _service(self, b, scope):
        fields = list(wire_fields(b))
        name = scope + "." + next(v.decode() for f, _, v in fields if f == 1)
        rpcs = []
        for f, _, v in fields:
            if f == 2:
                d = {ff: vv for ff, _, vv in wire_fields(v)}
                rpcs.append((d[1].decode(), d[2].decode().lstrip("."), d[3].decode().lstrip("."),
                             bool(d.get(6))))
        self.services[name] = rpcs

    def dump(self, out=sys.stdout):
        for name in sorted(self.services):
            out.write(f"service {name}\n")
            for rpc, i, o, st in self.services[name]:
                out.write(f"  rpc {rpc}({i}) -> {'stream ' if st else ''}{o}\n")
        for name in sorted(self.messages):
            out.write(f"message {name}\n")
            for no, (fname, t, tn, rep) in sorted(self.messages[name].items()):
                kind = tn.replace(PKG + ".", "") or TYPE_NAMES.get(t, t)
                out.write(f"  {no:>3} {fname}: {'repeated ' if rep else ''}{kind}\n")
        for name in sorted(self.enums):
            vals = ", ".join(f"{v}={k}" for k, v in sorted(self.enums[name].items()))
            out.write(f"enum {name}: {vals}\n")

    def decode(self, type_name, b):
        table = self.messages.get(type_name, {})
        out = {}
        for f, w, v in wire_fields(b):
            spec = table.get(f)
            if spec is None:
                out.setdefault("_unknown", []).append(f)
                continue
            name, t, tn, rep = spec
            if rep and w == 2 and t not in (T_STRING, T_BYTES, T_MESSAGE):
                vals = self._packed(t, tn, v)
            else:
                vals = [self._scalar(t, tn, w, v)]
            if rep:
                out.setdefault(name, []).extend(vals)
            else:
                out[name] = vals[-1]
        return out

    def _packed(self, t, tn, b):
        if t in (T_FLOAT,):
            return [round(x, 4) for x in struct.unpack(f"<{len(b) // 4}f", b)]
        if t in (T_DOUBLE,):
            return [round(x, 4) for x in struct.unpack(f"<{len(b) // 8}d", b)]
        vals, i = [], 0
        while i < len(b):
            v, i = _varint(b, i)
            vals.append(self._scalar(t, tn, 0, v))
        return vals

    def _scalar(self, t, tn, w, v):
        if t == T_MESSAGE:
            return self.decode(tn, v)
        if t == T_STRING:
            return v.decode("utf-8", "replace")
        if t == T_BYTES:
            return v.hex()
        if t == T_FLOAT:
            return round(struct.unpack("<f", v)[0], 4)
        if t == T_DOUBLE:
            return round(struct.unpack("<d", v)[0], 4)
        if t == T_BOOL:
            return bool(v)
        if t == T_ENUM:
            return self.enums.get(tn, {}).get(v, v)
        if t in (T_INT32, T_INT64) and v >= 1 << 63:
            return v - (1 << 64)
        if t in (T_SINT32, T_SINT64):
            return (v >> 1) ^ -(v & 1)
        return v


def load_schema(exe_path, cache_dir, channel):
    """Schema from the exe, cached in <cache_dir>/schema-<channel>.json keyed by exe size and mtime.
    Exe unreadable or without telemetry blobs: the cached schema if any, else None."""
    cache = os.path.join(cache_dir, f"schema-{channel}.json")
    saved = None
    try:
        with open(cache, encoding="utf-8") as fh:
            saved = json.load(fh)
        Schema.from_json(saved["schema"])
    except (OSError, ValueError, KeyError, TypeError):
        saved = None
    try:
        st = os.stat(exe_path)
        key = {"size": st.st_size, "mtime": int(st.st_mtime)}
    except OSError:
        key = None
    if saved and key and saved.get("size") == key["size"] and saved.get("mtime") == key["mtime"]:
        return Schema.from_json(saved["schema"])
    if key:
        try:
            blobs = descriptor_blobs(exe_path)
        except (OSError, ValueError):
            blobs = []
        if blobs:
            s = Schema.from_blobs(blobs)
            try:
                os.makedirs(cache_dir, exist_ok=True)
                tmp = cache + ".tmp"
                with open(tmp, "w", encoding="utf-8") as fh:
                    json.dump({**key, "schema": s.to_json()}, fh)
                os.replace(tmp, cache)
            except OSError:
                pass
            return s
    return Schema.from_json(saved["schema"]) if saved else None


# --- minimal gRPC client over cleartext HTTP/2 ------------------------------------

DATA, HEADERS, RST_STREAM, SETTINGS, PING, GOAWAY, WINDOW_UPDATE = 0, 1, 3, 4, 6, 7, 8
F_END_STREAM, F_ACK, F_END_HEADERS, F_PADDED, F_PRIORITY = 0x1, 0x1, 0x4, 0x8, 0x20
BIG_WINDOW = (1 << 31) - 1


def _frame(ftype, flags, stream, payload=b""):
    return struct.pack(">I", len(payload))[1:] + bytes([ftype, flags]) + struct.pack(">I", stream) + payload


def _hpack_int(value, prefix_bits, first):
    cap = (1 << prefix_bits) - 1
    if value < cap:
        return bytes([first | value])
    out = [first | cap]
    value -= cap
    while value >= 128:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value)
    return bytes(out)


def _hpack_str(s):
    b = s.encode()
    return _hpack_int(len(b), 7, 0) + b


def _request_headers(authority, path):
    # Literal header fields without indexing, no Huffman. Static-table names where they exist.
    h = bytes([0x83, 0x86])                       # :method POST, :scheme http
    h += _hpack_int(4, 4, 0) + _hpack_str(path)   # :path
    h += _hpack_int(1, 4, 0) + _hpack_str(authority)
    h += _hpack_int(31, 4, 0) + _hpack_str("application/grpc")
    for k, v in (("te", "trailers"), ("user-agent", "csr-telemetry")):
        h += b"\x00" + _hpack_str(k) + _hpack_str(v)
    return h


class GrpcStream:
    """One server-streaming call. Yields raw gRPC message payloads."""

    def __init__(self, host, port, path, request=b"", timeout=5.0):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.settimeout(None)
        self.buf = b""
        settings = struct.pack(">HI", 0x2, 0) + struct.pack(">HI", 0x4, BIG_WINDOW)
        out = b"PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n"
        out += _frame(SETTINGS, 0, 0, settings)
        out += _frame(WINDOW_UPDATE, 0, 0, struct.pack(">I", BIG_WINDOW - 65535))
        out += _frame(HEADERS, F_END_HEADERS, 1, _request_headers(f"{host}:{port}", path))
        out += _frame(DATA, F_END_STREAM, 1, b"\x00" + struct.pack(">I", len(request)) + request)
        self.sock.sendall(out)
        self.received = 0

    def close(self):
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.sock.close()

    def _read(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("game closed the connection")
            self.buf += chunk
        b, self.buf = self.buf[:n], self.buf[n:]
        return b

    def messages(self):
        grpc_buf = b""
        while True:
            hdr = self._read(9)
            ln = int.from_bytes(hdr[:3], "big")
            ftype, flags = hdr[3], hdr[4]
            stream = int.from_bytes(hdr[5:9], "big") & 0x7FFFFFFF
            payload = self._read(ln)
            if ftype == SETTINGS and not flags & F_ACK:
                self.sock.sendall(_frame(SETTINGS, F_ACK, 0))
            elif ftype == PING and not flags & F_ACK:
                self.sock.sendall(_frame(PING, F_ACK, 0, payload))
            elif ftype == GOAWAY:
                code = int.from_bytes(payload[4:8], "big")
                raise ConnectionError(f"GOAWAY code {code}: {payload[8:]!r}")
            elif ftype == RST_STREAM and stream == 1:
                raise ConnectionError(f"stream reset, code {int.from_bytes(payload[:4], 'big')}")
            elif ftype == HEADERS and stream == 1 and flags & F_END_STREAM:
                # Trailers. HPACK may be Huffman-coded, show the printable bits.
                text = re.sub(rb"[^ -~]+", b" ", payload).decode().strip()
                raise ConnectionError(f"stream ended by server, trailers: {text!r}")
            elif ftype == DATA and stream == 1:
                if flags & F_PADDED:
                    payload = payload[1:len(payload) - payload[0]]
                self.received += ln
                if self.received > 1 << 30:   # top the windows back up
                    inc = struct.pack(">I", self.received)
                    self.sock.sendall(_frame(WINDOW_UPDATE, 0, 0, inc) + _frame(WINDOW_UPDATE, 0, 1, inc))
                    self.received = 0
                grpc_buf += payload
                while len(grpc_buf) >= 5:
                    mlen = int.from_bytes(grpc_buf[1:5], "big")
                    if len(grpc_buf) < 5 + mlen:
                        break
                    if grpc_buf[0]:
                        raise ValueError("compressed gRPC message, not supported")
                    yield grpc_buf[5:5 + mlen]
                    grpc_buf = grpc_buf[5 + mlen:]
                if flags & F_END_STREAM:
                    return
