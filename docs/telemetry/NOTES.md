# Game telemetry stream: field notes

Observed on LIVE 4.10.201 (build 13220821), 2026-11-24, with `tools/sc_telemetry.py`.

## Turning it on

`user.cfg` in the channel folder:

```
grpc.client.server.enabled = true
```

The hyphen form (`grpc-client-server-enabled = 1`) is ignored. The env var
`GRPC_CLIENT_SERVER_ENABLED=true` also works but loses to `User CFG` when both are set.
The Game.log startup block shows the source:
`'GRPC_CLIENT_SERVER_ENABLED' was already set via 'User CFG ' (Ignored: 'Env Var  ')`.

Server: `127.0.0.1:2942`, cleartext HTTP/2, `/sc.game.telemetry.v1.GameTelemetryService/TelemetryStream`.

## Stream behaviour

- About 14-15 frames/s (median gap 63 ms), not the 50 ms the setting suggests.
- Every frame is a full snapshot. proto3 drops default values, so a missing field means
  false / 0 / absent. `vehicle` and `weapons` are missing when not seated in a vehicle.
- `events` only appears on the frame where something happened (`ACTOR_ENTER_SEAT` on sitting down).
- `altitude` reads `3.4028234663852886e+38` (FLT_MAX) when there is no reference body. Treat >= 1e38 as none.
- `oxygen` is a 0-1 fraction. health, hunger, thirst are 0-100. `body_temperature.max` is 100.
- `vehicle.state.name` and weapon names carry entity ids (`DRAK_Marauder_948031347757`).
  `class_name` is the clean one (`DRAK_Marauder`). Weapon names need the id stripped.
- `seat_name` is the seat entity (`DRAK_Marauder_Seat_Pilot_948031347813`).
- LIVE has no master-mode field. Switching to NAV shows as `annunciators.shld` going OFF and
  `annunciators.qdrv` going NOMINAL in the same frame, with shield power dropping out.
- `g_meter.max` is the ship's G limit (11.0-11.8 on the Marauder), not a session peak.
- `boost.critical` is true when reserves are near empty. `boost.reserves` went down to 0.0008.
- `hydrogen_fuel` / `quantum_fuel` are 0-1 fractions.
- Thirst drops about 1 point per 90 s while flying.

## Not in the stream

Position, location name, target, kills, missions, inventory, cargo. The Game.log stays the source for those.
