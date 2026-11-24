"""The Kestrel K-7 Flight Slate: CSR's live view page.

SLATE_MODEL_JS is a pure function that turns the /api/telemetry/live payload into the
model the page draws. It runs in Node for tests (tests/test_slate_model.py)."""

SLATE_MODEL_JS = r"""
function annunciatorLevel(v){
  v = String(v == null ? '' : v).toUpperCase();
  if (v === 'NOMINAL') return 'ok';
  if (v.indexOf('MAJOR') === 0) return 'maj';
  if (v.indexOf('MINOR') === 0) return 'min';
  return 'off';
}
function slateModel(payload, ship){
  payload = payload || {};
  const snap = payload.snapshot || {};
  const num = v => (typeof v === 'number' && isFinite(v)) ? v : null;
  const pct = v => { v = num(v); return v == null ? null : Math.round(v <= 1.0001 ? v * 100 : v); };
  const gauge = g => g && num(g.current) != null ? num(g.current) : null;
  const live = payload.state === 'live' && Object.keys(snap).length > 0;
  const actor = snap.actor || {}, ast = actor.state || {}, vit = actor.vitals || {}, env = actor.environment || {};
  const veh = (snap.vehicle || {}).state || null;
  const loco = String(ast.locomotion || '').toUpperCase(), role = String(ast.crew_role || '').toUpperCase();
  let seat = null;
  if (loco === 'SEATED') seat = role === 'GUNNER' ? 'gunner' : role === 'CREW' ? 'crew' : 'pilot';
  else if (loco === 'ON_FOOT') seat = 'foot';
  else if (loco === 'EVA') seat = 'eva';
  const inSeat = seat === 'pilot' || seat === 'gunner' || seat === 'crew';
  const fl = (veh && veh.flight) || {}, ins = fl.instruments || {}, fst = fl.state || {}, set = fl.settings || {};
  const ann = {};
  ['pwr','wpn','thr','shld','cool','qdrv','radr','prox','hit','misl'].forEach(k => {
    ann[k] = annunciatorLevel(veh && veh.annunciators ? veh.annunciators[k] : null);
  });
  const big = v => { v = num(v); return v == null || v >= 1e38 ? null : v; };
  const dep = v => v == null ? null : String(v).toUpperCase();
  const mm = veh && veh.master_mode ? String(veh.master_mode.mode || '').toUpperCase() : '';
  let master = mm.indexOf('NAV') === 0 ? 'nav' : mm === 'SCM' ? 'scm' : mm === 'STEALTH' ? 'stealth' : null;
  // 4.10 sends no master mode. NAV switches the shields off and the quantum drive on, SCM the reverse.
  let masterGuess = false;
  if (!master && inSeat && veh && veh.annunciators) {
    if (ann.qdrv !== 'off' && ann.shld === 'off') master = 'nav';
    else if (ann.qdrv === 'off' && ann.shld !== 'off') master = 'scm';
    masterGuess = !!master;
  }
  // engines report a fault while quantum travel holds them: not damage
  if (master === 'nav' && ann.thr !== 'off') ann.thr = 'ok';
  const ifcs = {};
  Object.keys(set).forEach(k => {
    const v = set[k];
    if (v && typeof v === 'object') ifcs[k] = {enabled: !!v.enabled, active: !!v.active};
  });
  const sh = veh && veh.shields;
  const names = payload.names || {};
  const wf = snap.weapons || {};
  const weapons = (inSeat ? (wf.weapons || []) : []).map(w => {
    const cls = String(w.name || '').replace(/_\d{6,}$/, '');
    const m = /_S(\d+)/i.exec(cls);
    return [names[cls.toLowerCase()] || cls, m ? 'S' + m[1] : '', !!w.is_powered, !!w.ready_to_fire,
            !!w.contained_in_preset];
  });
  const cm = {decoy: null, noise: null};
  (inSeat ? (wf.countermeasures || []) : []).forEach(c => {
    const t = String(c.type || '').toUpperCase();
    if (t.indexOf('DECOY') >= 0) cm.decoy = num(c.amount) || 0;
    if (t.indexOf('NOISE') >= 0) cm.noise = num(c.amount) || 0;
  });
  const ev = {downed: 0, dead: 0, respawn: 0, seat: 0};
  (payload.events || []).forEach(e => {
    const t = String(e.type || '');
    if (t.indexOf('DOWNED') >= 0) ev.downed++;
    else if (t.indexOf('RESPAWN') >= 0) ev.respawn++;
    else if (t.indexOf('DEAD') >= 0) ev.dead++;
    else if (t.indexOf('ENTER_SEAT') >= 0) ev.seat++;
  });
  const emptyLog = {place: null, jurisdiction: null, armistice: null, notices: [], contracts: [], kills: [],
                    injuries: {}, loadout: {}, blueprints: 0, seq: 0};
  const doors = veh && veh.doors ? veh.doors : null;
  const o2 = gauge(vit.oxygen);
  return {
    live: live,
    wait: live ? '' : 'Waiting for the game',
    seat: seat,
    ship: inSeat && veh ? {cls: veh.class_name || null,
                           name: String(veh.name || veh.class_name || '').replace(/_\d{6,}$/, ''),
                           type: veh.type || null, maker: veh.manufacturer || null,
                           lights: veh.lights_enabled == null ? null : !!veh.lights_enabled,
                           doorsOpen: doors ? String(doors.open_state || '').indexOf('OPEN') >= 0 : null,
                           doorsLocked: doors ? String(doors.lock_state || '').toUpperCase() === 'LOCKED' : null} : null,
    flight: {speed: num(ins.speed), alt: big(ins.altitude), radarAlt: big(ins.radar_altitude),
             vsi: num(ins.vertical_speed_indicator), density: num(ins.atmospheric_density),
             heading: num(ins.heading), g: ins.g_meter ? num(ins.g_meter.current) : null,
             gMax: ins.g_meter ? num(ins.g_meter.max) : null, h2: pct(ins.hydrogen_fuel), qt: pct(ins.quantum_fuel),
             boost: ins.boost ? {reserves: pct(ins.boost.reserves), critical: !!ins.boost.critical,
                                 engaged: !!ins.boost.engaged} : null,
             gear: dep(fst.gear), vtol: dep(fst.vtol), wings: dep(fst.transformation),
             landed: fst.is_landed == null ? null : !!fst.is_landed,
             master: master, masterGuess: masterGuess,
             spooled: veh && veh.master_mode ? !!veh.master_mode.spooled : null},
    ifcs: ifcs,
    power: veh && veh.power ? Object.assign({}, veh.power) : {},
    ann: ann,
    threat: ann.misl === 'maj' || ann.hit === 'maj',
    shields: {faces: sh && Array.isArray(sh.faces) && sh.faces.length ? sh.faces.map(pct) : null,
              bubble: sh ? pct(sh.level) : null},
    hull: veh ? pct(veh.health) : null,
    weapons: weapons,
    preset: wf.preset_name || null,
    gimbalsLocked: wf.settings ? !!wf.settings.gimbals_locked : null,
    staggered: wf.settings ? !!wf.settings.staggered_fire : null,
    weaponSettings: wf.settings || {},
    cm: cm,
    vitals: {hp: gauge(vit.health), o2: o2 == null ? null : Math.round(o2 <= 1.0001 ? o2 * 100 : o2),
             food: gauge(vit.hunger), water: gauge(vit.thirst), temp: gauge(vit.body_temperature),
             hr: num(vit.heart_rate), gravity: num(vit.gravity), pressure: num(vit.pressure),
             intox: num(vit.intoxication_level) || 0},
    env: {temp: num(env.external_temperature), rad: num(env.ambient_radiation_rate),
          breathable: env.breathable_atmosphere == null ? null : !!env.breathable_atmosphere},
    wallet: num(ast.uec_balance),
    events: ev,
    log: payload.log || emptyLog,
    shipData: ship || null
  };
}
if (typeof module !== 'undefined') module.exports = {slateModel, annunciatorLevel};
"""

SLATE_CSS = r"""
:root{--bg:#050d12;--panel:rgba(6,20,26,.78);--line:#1f4c5e;--c:#5bd1e6;--c2:#2c9fb5;--txt:#bfeaf3;--dim:#6fb8c8;--mute:#3f6b78;--a:#ffb347;--r:#ff5468;--g:#5be69a;--hi:#eafcff}
*{box-sizing:border-box}
html,body{margin:0;height:100%;background:var(--bg);color:var(--txt);font:13px/1.3 "Bahnschrift","Segoe UI",system-ui,sans-serif;overflow:hidden}
body{background:radial-gradient(120% 80% at 50% 0%,#0d2a36 0%,#050d12 65%)}

svg.ic{width:20px;height:20px;fill:none;stroke:currentColor;stroke-width:1.7;stroke-linecap:round;stroke-linejoin:round;flex:none}
svg.ic.sm{width:14px;height:14px}
button{font:inherit;color:inherit;background:none;border:0;cursor:pointer}
.cut{clip-path:polygon(8px 0,100% 0,100% calc(100% - 8px),calc(100% - 8px) 100%,0 100%,0 8px)}

/* ---------- shell ---------- */
#app{position:fixed;inset:0;display:grid;grid-template-columns:76px 1fr 300px;grid-template-rows:auto 1fr;gap:0 10px;padding:10px 12px 12px}
#app.phone{inset:0 auto 0 50%;width:390px;transform:translateX(-50%);border-left:1px solid var(--line);border-right:1px solid var(--line)}
.threat{grid-column:1/4;display:flex;align-items:center;gap:10px;height:30px;padding:0 12px;margin-bottom:10px;font-size:12px;letter-spacing:.08em;color:var(--dim);border:1px solid var(--line);background:rgba(6,20,26,.6)}
.threat .chip{padding:2px 7px;font-weight:700;letter-spacing:.06em}
.threat.hot{color:#ffd0d5;border-color:#7a1d26;background:linear-gradient(90deg,rgba(255,84,104,.38),rgba(255,84,104,.06))}
.chip.r{background:var(--r);color:#fff;box-shadow:0 0 10px var(--r)}.chip.a{background:var(--a);color:#000}.chip.c{border:1px solid var(--line);color:var(--dim)}
.threat .sep{color:var(--mute)}
.threat .clock{margin-left:auto;color:var(--dim)}
.demo{display:flex;gap:4px;margin-left:10px}
.demo button{font-size:10px;letter-spacing:.1em;padding:3px 7px;border:1px solid var(--line);color:var(--dim)}
.demo button.on{background:#123744;color:var(--c);border-color:var(--c2)}

/* bezel */
.bezel{display:flex;flex-direction:column;gap:8px}
.osb{position:relative;display:flex;flex-direction:column;align-items:center;gap:3px;padding:10px 2px 8px;font-size:10px;letter-spacing:.12em;color:var(--c);background:linear-gradient(#123744,#0a2129);border:1px solid #2b6b80}
.osb.on{background:linear-gradient(var(--c),var(--c2));color:#03141a;font-weight:700;box-shadow:0 0 14px rgba(91,209,230,.6)}
.osb .bdg{position:absolute;top:3px;right:4px;background:var(--a);color:#000;font-size:9px;font-weight:700;padding:0 4px}

/* main page */
main{position:relative;overflow:auto;min-height:0}
.page{display:none;height:100%}.page.on{display:flex;flex-direction:column;gap:10px}
.panel{background:var(--panel);border:1px solid var(--line);padding:10px 12px}
.ph{font-size:10px;letter-spacing:.16em;color:var(--dim);margin-bottom:8px;display:flex;align-items:center;gap:6px}
.ph .r{margin-left:auto;letter-spacing:.04em}
.big{font-size:40px;font-weight:600;color:var(--hi);line-height:1}
.unit{font-size:11px;color:var(--dim);letter-spacing:.04em}
.mute{color:var(--mute)}

/* annunciators */
.ann{display:grid;grid-template-columns:repeat(10,1fr);gap:4px}
.ann div{font-size:10px;text-align:center;padding:5px 0;border:1px solid #1e4452;color:var(--mute);background:#071a22;letter-spacing:.06em}
.ann .ok{color:var(--g);border-color:#1d5a3b}.ann .min{color:#03141a;background:var(--a);border-color:var(--a)}
.ann .maj{color:#fff;background:var(--r);border-color:var(--r);box-shadow:0 0 8px var(--r)}

/* ship page grid */
.shipgrid{display:grid;grid-template-columns:1fr 1.35fr 1fr;gap:10px;flex:1;min-height:0}
#page-ship .defense svg{max-height:min(46vh,420px)!important;height:auto}
#page-ship .deck .btn{min-height:clamp(62px,10vh,96px)}
#page-ship .deck svg.ic{width:24px;height:24px}
#page-ship .pips{height:clamp(96px,30vh,260px)}
#page-ship .pip i{height:clamp(8px,4.2vh,40px)}
.demo{position:fixed;top:6px;right:8px;z-index:60;background:rgba(5,13,18,.9);padding:3px;border:1px solid var(--line)}
.threat .clock{margin-right:0}
#app.phone .shipgrid,#app.portrait .shipgrid{flex:none}
#app.phone .shipgrid>.panel:first-child,#app.portrait .shipgrid>.panel:first-child{grid-column:1/3}
#app.phone #page-ship .defense svg,#app.portrait #page-ship .defense svg{max-height:30vh!important}
#app.phone #page-ship .deck .btn,#app.portrait #page-ship .deck .btn{min-height:58px}
#app.phone,#app.portrait{padding-top:8px}
#app{padding-top:40px}
/* summary row on MAIN */
.sumrow{display:grid;grid-template-columns:1.3fr 1fr 1fr;gap:10px;flex:none}
.sumrow .panel{padding:8px 12px}
.sumrow .title{font-size:15px;color:var(--hi);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.sumrow .line{font-size:12px;color:var(--txt);margin-top:3px;display:flex;align-items:center;gap:6px}
.mini{display:grid;grid-template-columns:auto 1fr auto;gap:4px 8px;align-items:center;font-size:10px;letter-spacing:.08em;color:var(--dim)}
.mini .bar{height:6px}.mini b{color:var(--hi);font-weight:600;font-size:11px;letter-spacing:0}
.injury{display:inline-block;margin-top:5px;font-size:10px;padding:2px 6px;border:1px solid var(--a);color:var(--a);letter-spacing:.06em}
.zoneU{color:var(--a)}.zoneA{color:var(--g)}
#page-ship .pwr .pips{height:clamp(80px,22vh,200px)}
#page-ship .pip i{height:clamp(7px,3vh,30px)}
/* on foot */
#app .foot{display:none}
#app.onfoot .seat{display:none}
#app.onfoot .foot{display:grid}
.footgrid{grid-template-columns:1.2fr 1fr;gap:10px;flex:1;min-height:0}
.vbig{display:grid;grid-template-columns:repeat(2,1fr);gap:14px 22px}
.vbig .bar{height:12px}
.vbig .kv b{font-size:20px}
.emotes{grid-template-columns:repeat(6,1fr);gap:8px}
#app.phone .sumrow,#app.portrait .sumrow{grid-template-columns:1fr}
#app.phone .footgrid,#app.portrait .footgrid{grid-template-columns:1fr}
#app.phone .emotes,#app.portrait .emotes{grid-template-columns:repeat(3,1fr)}
#app.phone .vbig,#app.portrait .vbig{gap:8px 14px}

/* ======== TABLET + MAP-FIRST MAIN ======== */
body.tabletmode{background:#02070a}
#app.tablet{inset:auto;left:50%;top:50%;width:var(--lw,1180px);height:var(--lh,820px);transform:translate(-50%,-50%) scale(var(--s,1));transform-origin:center;border:10px solid #0b1418;border-radius:28px;box-shadow:0 0 0 2px #1a2b32,0 20px 80px #000;padding:12px 14px 14px}

#app.tablet{grid-template-columns:72px 1fr 250px}
#app.tablet.feedpage{grid-template-columns:72px 1fr}
#app.tablet.portrait{grid-template-columns:1fr}
#page-ship.on{gap:8px}
.hero{display:grid;grid-template-columns:1.9fr 1fr;gap:10px;flex:1;min-height:0}
.mapp .ph .r{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0}.mapp .ph .r:has(button){overflow:visible}
.dist{min-width:132px;padding:5px 8px}.dr{font-size:11px}
#page-ship .mini-pips{display:grid!important;grid-template-columns:repeat(3,1fr);gap:6px 12px}
#page-ship .mini-pips .pip{justify-content:flex-start}
#page-ship .mini-pips .pip span{width:30px;margin:0}
.mapp{display:flex;flex-direction:column;min-height:0;padding:8px 10px}
.mapp .ph{margin-bottom:4px}
.sysbtns{display:flex;gap:2px}
.sysbtn{font-size:10px;letter-spacing:.14em;padding:4px 8px;border:1px solid var(--line);color:var(--dim);background:none;cursor:pointer;font-family:inherit}
.sysbtn.on{color:var(--hi);border-color:var(--c2);background:#123744}
.sysbtn i{font-style:normal;font-size:9px;margin-left:4px;color:#b58cff}
.mapmodes{display:flex;gap:4px;margin-left:10px}
.mapmodes button{font-size:9px;letter-spacing:.12em;padding:4px 8px;border:1px solid var(--line);color:var(--dim)}
.mapmodes button.on{background:#123744;color:var(--c);border-color:var(--c2)}
.mapwrap{position:relative;flex:1;min-height:0}
.mapwrap svg{position:absolute;inset:0;width:100%;height:100%}
.dist{position:absolute;right:4px;bottom:4px;background:rgba(5,13,18,.85);border:1px solid var(--line);padding:6px 10px;min-width:150px}
.dist .dh{font-size:9px;letter-spacing:.16em;color:var(--dim);margin-bottom:4px}
.mapwrap svg{width:calc(100% - 142px)!important}
.mapwrap .dist{top:0;bottom:0;right:0;width:156px;min-width:0;background:none;border:0;border-left:1px solid var(--line);padding:4px 0 4px 10px;display:flex;flex-direction:column;gap:2px}
.mapwrap .dist .dzone{margin-top:auto}
.mapwrap .dist{overflow:hidden}
.dr{display:flex;align-items:center;gap:7px;font-size:12px;padding:1px 0}.dr b{margin-left:auto;color:var(--hi);font-weight:600}
.sw{width:8px;height:8px;border-radius:50%}
.sidecol{display:flex;flex-direction:column;gap:10px;min-height:0}
#app .sidecol.foot{display:none}#app.onfoot .sidecol.foot{display:flex}
.sidecol .defense{flex:1;min-height:0;display:flex;flex-direction:column}
.sidecol .defense svg{flex:1;min-height:0;max-height:230px!important}
.frow{display:flex;align-items:baseline;justify-content:space-between}
.fbars{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-top:4px}
.fbars .kv{margin:6px 0 3px}
#page-ship .mini-pips{height:auto;margin-top:8px;justify-content:space-between}
#page-ship .mini-pips .pip{flex-direction:row;gap:2px;align-items:center}
#page-ship .mini-pips .pip i{width:7px;height:12px}
#page-ship .mini-pips .pip span{margin:0 4px 0 0;order:9}
.sumrow{grid-template-columns:1.2fr 1fr}
#app.onfoot .sumrow{grid-template-columns:1.2fr 1fr}
#page-ship .deck .btn{min-height:68px}
/* rings */
.rings{display:grid;gap:8px}
.rings.small{grid-template-columns:repeat(4,1fr)}
.rings.big{grid-template-columns:repeat(4,1fr);gap:14px 8px;margin:6px 0 10px}
.ring{display:flex;flex-direction:column;align-items:center;gap:2px}
.ring svg.g{width:52px;height:52px}
.rings.big .ring svg.g{width:84px;height:84px}
.ring .val{font-size:12px;color:var(--hi);font-weight:600}.rings.big .ring .val{font-size:15px}
.ring .lab{font-size:9px;letter-spacing:.12em;color:var(--dim)}
.ring .trk{fill:none;stroke:#0f2a33;stroke-width:5}
.ring .arc{fill:none;stroke-width:5;stroke-linecap:round;transform:rotate(-90deg);transform-origin:50% 50%;transition:stroke-dashoffset .4s}
.ring svg.ico{fill:none;stroke-width:1.6;stroke-linecap:round;stroke-linejoin:round}
.pulse{animation:beat 1s infinite}
@keyframes beat{0%,100%{transform:scale(1)}15%{transform:scale(1.12)}}
/* portrait tablet */
#app.portrait .hero{grid-template-columns:1fr;grid-template-rows:minmax(300px,1.2fr) auto}
#app.portrait .sidecol.seat{display:grid;grid-template-columns:1.1fr 1fr}
#app.onfoot.portrait .sidecol.seat{display:none}
#app.portrait .sidecol .defense svg{max-height:170px!important}
#app.portrait .sumrow{grid-template-columns:1.2fr 1fr}
#app.portrait .deck{grid-template-columns:repeat(6,1fr)}
#app.portrait .rings.big{grid-template-columns:repeat(4,1fr)}
#app.tablet.portrait .rail{grid-row:2;margin-bottom:8px}
#app.tablet.portrait .rail .needbox{display:block}
#app.tablet.portrait .rail .need{display:inline-flex;width:calc(33.3% - 6px);margin-right:6px;vertical-align:top}
#app.tablet.portrait .rail .need:nth-child(n+4){display:inline-flex}
/* damage strip + combat damage panel */
.ann{display:flex!important;flex-wrap:wrap;gap:4px}
.ann>div{padding:5px 10px!important;letter-spacing:.08em}
.ann .dmg i{font-style:normal;opacity:.85;font-size:9px;margin-left:4px}
.ann .okall{flex:1;text-align:left!important}
.ann .offall{color:var(--mute);border:1px dashed var(--line)!important;background:none!important}
.dmgp{display:none}
.mapp.fight .dmgp{display:flex;flex-direction:column;gap:8px;flex:1;min-height:0;overflow:auto}
.mapp.fight .mapwrap{display:none}
.mapp.fight{border-color:#7a1d26;box-shadow:inset 0 0 20px rgba(255,84,104,.12)}
.mapbtn{font-size:10px;letter-spacing:.12em;padding:3px 10px;border:1px solid var(--c2);color:var(--c);background:#0b2330}
.drow{display:grid;grid-template-columns:44px 1fr 120px;gap:10px;align-items:center;padding:10px 12px;border:1px solid var(--line);background:#071a22}
.drow.maj{border-color:var(--r);background:linear-gradient(90deg,rgba(255,84,104,.22),rgba(7,26,34,.9))}
.drow.min{border-color:var(--a);background:linear-gradient(90deg,rgba(255,179,71,.14),rgba(7,26,34,.9))}
.drow .dic{width:44px;height:44px;display:grid;place-items:center;border:1px solid currentColor}
.drow.maj .dic{color:var(--r)}.drow.min .dic{color:var(--a)}
.drow .dic svg.ic{width:26px;height:26px}
.drow .dn{font-size:15px;color:var(--hi);letter-spacing:.06em}
.drow .dsev{font-size:9px;letter-spacing:.14em;padding:1px 6px;margin-left:6px;vertical-align:2px}
.drow.maj .dsev{background:var(--r);color:#fff}.drow.min .dsev{background:var(--a);color:#000}
.drow .dd{font-size:13px;color:var(--txt);margin-top:2px}.drow .ds{font-size:10px;color:var(--dim)}
.dbar{height:8px;background:#0f2a33;position:relative}.dbar i{position:absolute;inset:0 auto 0 0}
.drow.maj .dbar i{background:var(--r)}.drow.min .dbar i{background:var(--a)}
.dok,.dloc{display:flex;align-items:center;gap:8px;font-size:12px;color:var(--g);padding:6px 4px}
.dloc{color:var(--txt);margin-top:auto;border-top:1px solid var(--line);padding-top:8px}
/* paper doll */
.youtop{display:grid;grid-template-columns:1.7fr 1fr;gap:10px}
.dollgrid{display:grid;grid-template-columns:1fr 150px 1fr;gap:12px;align-items:center}
.slots{display:flex;flex-direction:column;gap:7px}
.slot{display:flex;gap:9px;align-items:center;padding:7px 9px;background:linear-gradient(90deg,rgba(18,55,68,.7),rgba(8,25,35,.6));border:1px solid #2b6b80;min-height:52px}
.slots.right .slot{flex-direction:row-reverse;text-align:right;background:linear-gradient(270deg,rgba(18,55,68,.7),rgba(8,25,35,.6))}
.slot .sic{width:34px;height:34px;flex:none;display:grid;place-items:center;border:1px solid var(--line);background:#071a22;color:var(--c)}
.slot .sic svg.ic{width:22px;height:22px}
.slot .stx{min-width:0}
.slot .sl{font-size:9px;letter-spacing:.14em;color:var(--dim);display:flex;gap:6px;align-items:center}
.slots.right .slot .sl{justify-content:flex-end}
.slot .sn{font-size:13px;color:var(--hi);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.slot .ss{font-size:10px;color:var(--dim);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.slot.empty{opacity:.45;border-style:dashed;background:none}
.ichip{font-size:9px;letter-spacing:.1em;padding:0 5px;border:1px solid;text-transform:uppercase}
.dollfig{display:flex;flex-direction:column;align-items:center;gap:6px}
.dollsvg{width:130px;height:250px}
.dollnote{font-size:11px;text-align:center;line-height:1.4}
.carry{display:grid;grid-template-columns:repeat(5,1fr);gap:7px;margin-top:10px}
.ctile{display:flex;align-items:center;gap:6px;padding:7px 8px;border:1px solid var(--line);background:#071a22;font-size:11px}
.ctile span{flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.ctile b{color:var(--hi)}
.youside .rings.big{grid-template-columns:repeat(2,1fr)!important;gap:12px 6px}
.youside .ring svg.g{width:62px!important;height:62px!important}
.youtop{grid-template-columns:minmax(0,1fr) 200px!important}
.youtop>*{min-width:0}
#app:not(.phone):not(.portrait) .youside{display:flex;flex-direction:column;gap:10px;min-height:0}
#app:not(.phone):not(.portrait) .youside>#suitPanel{flex:1;display:flex;flex-direction:column}
#suitPanel .suitscale{margin-top:auto;padding-top:8px}
#app:not(.phone):not(.portrait) #page-you .youtop{flex:1;min-height:0}#app:not(.phone):not(.portrait) #page-you .youtop>.doll{min-height:0}
.dollgrid{grid-template-columns:minmax(0,1fr) 130px minmax(0,1fr)!important}
.dollsvg{width:120px!important;height:230px!important}
#app.portrait .youtop{grid-template-columns:1fr!important}
.carry{grid-template-columns:repeat(3,1fr)}
#page-you.on{gap:10px}
#app.portrait .youside .rings.big{grid-template-columns:repeat(4,1fr)!important}
#app.portrait .youtop{grid-template-columns:1fr}
#app.portrait .carry{grid-template-columns:repeat(3,1fr)}
#app.phone .youtop{grid-template-columns:1fr!important;gap:8px}
#app.phone .doll .ph .r{display:none}
#app.phone .dollgrid{grid-template-columns:minmax(0,1fr) minmax(0,1fr)!important;grid-template-areas:"fig fig" "l r";gap:6px 6px}
#app.phone .dollgrid{align-items:start}#app.phone .youside{order:-1}
#app.phone .dollfig{grid-area:fig}#app.phone #slotsL{grid-area:l}#app.phone #slotsR{grid-area:r}
#app.phone .dollsvg{width:84px!important;height:160px!important}
#app.phone .dollfig{flex-direction:row;justify-content:center;gap:12px}
#app.phone .dollnote{text-align:left;font-size:10px}
#app.phone .slots{gap:5px}
#app.phone .slot{gap:6px;padding:5px 6px;min-height:42px}
#app.phone .slot .sic{width:26px;height:26px}#app.phone .slot .sic svg.ic{width:16px;height:16px}
#app.phone .slot .sn{font-size:12px}
#app.phone .slots.right .slot{flex-direction:row;text-align:left;background:linear-gradient(90deg,rgba(18,55,68,.7),rgba(8,25,35,.6))}#app.phone .slots.right .slot .sl{justify-content:flex-start}
#app.phone .carry{grid-template-columns:minmax(0,1fr) minmax(0,1fr)!important;gap:5px;margin-top:6px}
#app.phone .youside .rings.big{grid-template-columns:repeat(4,1fr)!important;gap:8px 4px}
#app.phone .youside .ring svg.g{width:50px!important;height:50px!important}
/* loadout */
.loadcard .lrow,.loadfull .lrow{display:flex;align-items:center;gap:8px;padding:4px 0;font-size:12px;border-bottom:1px solid rgba(31,76,94,.35)}
.loadcard .lrow .unit{margin-left:auto}
.loadfull{display:grid;grid-template-columns:1fr 1.7fr 1fr 1fr;gap:14px}
.lsec .lh{font-size:9px;letter-spacing:.16em;color:var(--dim);margin-bottom:4px}
.lk{width:72px;flex:none;font-size:10px;letter-spacing:.08em;color:var(--dim)}
#app.portrait .loadfull{grid-template-columns:1fr 1fr}
#app.onfoot:not(.portrait) .sidecol.foot{overflow:auto}
#app.portrait.onfoot .sidecol.foot .loadcard{grid-column:1/3}

/* ======== PHONE ======== */
.phmain,.phonly{display:none!important}
#app.phone{inset:auto!important;left:50%!important;top:50%!important;width:var(--pw)!important;height:var(--ph)!important;transform:translate(-50%,-50%) scale(var(--s,1))!important;
  border:9px solid #0b1418;border-radius:34px;box-shadow:0 0 0 2px #1a2b32,0 20px 80px #000;padding:10px 8px 8px!important;
  grid-template-columns:1fr!important;grid-template-rows:auto 1fr auto!important;gap:6px!important}
#app.phone .threat{grid-column:1!important;height:26px;font-size:10px;margin:0;padding:0 8px}
#app.phone .threat .clock,#app.phone .threat .long{display:none}
#app.phone .rail{display:none!important}
#app.phone main{grid-row:2!important;overflow-y:auto;overflow-x:hidden}
#app.phone .bezel{grid-row:3!important;display:grid!important;grid-template-columns:repeat(5,1fr)!important;gap:4px;margin:0!important}
#app.phone .bezel .osb[data-p="sys"],#app.phone .bezel .osb[data-p="ppl"],#app.phone .bezel .osb[data-p="you"]{display:none}
#app.phone .phonly{display:flex!important}
#app.phone .osb{padding:6px 0 4px;font-size:8px;gap:2px}
#app.phone .osb svg.ic{width:18px;height:18px}
#app.phone #page-ship.on>*:not(.phmain){display:none!important}
#app.phone .phmain{display:flex!important;flex-direction:column;gap:6px}
#moreSheet{position:absolute;left:8px;right:8px;bottom:62px;z-index:20;display:grid;grid-template-columns:repeat(3,1fr);gap:6px;background:#06141a;border:1px solid var(--c2)}
.phglance{display:grid;grid-template-columns:1fr auto;gap:6px;align-items:center;background:var(--panel);border:1px solid var(--line);padding:8px 10px}
.phspd{display:flex;flex-direction:column}.phspd .big{font-size:40px}
.phdef{display:flex;align-items:center;gap:6px}
.msh{width:96px;height:96px}
.phdv{display:grid;grid-template-columns:auto auto;gap:0 6px;align-items:baseline;font-size:18px;font-weight:700}
.phdv .unit{font-size:9px;letter-spacing:.12em}
.phlights{display:flex;flex-wrap:wrap;gap:4px}
.pl{font-size:9px;letter-spacing:.08em;padding:3px 6px;border:1px solid var(--line)}
.pl.min{background:var(--a);color:#000;border-color:var(--a)}.pl.maj{background:var(--r);color:#fff;border-color:var(--r)}.pl.ok{color:var(--g);border-color:#1d5a3b}
.phrow{display:flex;align-items:center;gap:8px;width:100%;text-align:left;background:var(--panel);border:1px solid var(--line);padding:9px 10px;font-size:12px;color:var(--txt)}
.phrow b{color:var(--hi)}.phrow.wait{border-left:3px solid var(--a);background:rgba(255,179,71,.08);color:#ffe2b8}.phrow.dim{opacity:.7;border-style:dashed}
.phbar{flex:1;height:5px;background:#0f2a33;position:relative;min-width:40px}.phbar i{position:absolute;inset:0 auto 0 0;background:var(--c)}
.phdmg{flex:1;min-height:80px;background:var(--panel);border:1px solid #ff5a6a55;display:flex;flex-direction:column;gap:6px;padding:8px;overflow:auto}
.phdh{display:flex;justify-content:space-between;align-items:center;font-size:11px;letter-spacing:.12em;color:#ff8a94}
.phdr{display:grid;grid-template-columns:1fr auto;gap:4px 8px;align-items:center;text-align:left;background:#0a1418;border:1px solid var(--line);border-left:3px solid #ffb347;padding:7px 9px;color:inherit;font:inherit;cursor:pointer}
.phdr.maj{border-left-color:#ff5a6a;background:#1a0b0e}
.phdn{font-size:13px;font-weight:600;letter-spacing:.06em}.phdr.maj .phdn{color:#ff8a94}.phdr.min .phdn{color:#ffb347}
.phdd{font-size:12px;color:var(--dim)}
.phdr .phbar{grid-column:1/-1;width:100%}.phdr.maj .phbar i{background:#ff5a6a}.phdr.min .phbar i{background:#ffb347}
.phdok{font-size:11px;color:#5be69a;margin-top:auto}
#phmain.tight .phlights,#phmain.tight .phdok{display:none}
#phmain.tight .phdmg{gap:4px;padding:6px}
#phmain.tight .phdr{grid-template-columns:auto 1fr 56px;padding:4px 8px;gap:2px 8px}
#phmain.tight .phdr{position:relative}#phmain.tight .phdr::after{content:"";position:absolute;inset:-6px -1px}
#phmain.tight .phdr .phbar{grid-column:auto;width:56px}
#phmain.tight .dguns.ph{padding:4px 8px}#phmain.tight .dguns.ph .dgship{display:none}
.phback{position:absolute;bottom:6px;right:6px;z-index:2}
.phmap{height:150px;background:var(--panel);border:1px solid var(--line);position:relative}
.phmap svg{position:absolute;inset:0;width:100%;height:100%}
#app.phone .phmap{display:flex;flex-direction:column}
.phsvg{position:relative;flex:1;min-height:0}
.phmhead{display:flex;gap:2px;align-items:center;padding:4px;border-bottom:1px solid var(--line)}
.phmsys{margin-left:auto;margin-right:6px;font-size:9px;letter-spacing:.14em;color:var(--dim)}
#phmain.mapbig>:not(.phmap){display:none!important}#phmain.mapbig>.phmap{flex:1}
#phmain.mapbig .phsvg{flex:none;aspect-ratio:440/420}
.phdist{flex:1;min-height:0;overflow:auto;padding:6px 10px;border-top:1px solid var(--line);display:flex;flex-direction:column;gap:3px}
.phdist .dr{font-size:13px}.phdist .dzone{margin-top:auto}
.phmhead button{font-size:9px;letter-spacing:.12em;padding:3px 7px;min-height:28px;border:1px solid var(--line);color:var(--dim);background:rgba(5,13,18,.85);font-family:inherit;cursor:pointer}
.phmhead button.on{background:#123744;color:var(--c);border-color:var(--c2)}
.phdeck{display:grid;grid-template-columns:repeat(4,1fr);gap:6px}
#app.phone .phdeck .btn{min-height:62px;font-size:9px}
.phglance.foot{grid-template-columns:1fr auto}
.phglance.foot .pho2{grid-column:1/3;display:flex;align-items:baseline;gap:10px}
.phrings{display:grid;grid-template-columns:repeat(4,1fr);gap:4px}
.phrings .ring svg.g{width:44px;height:44px}
#app.phone.phshort .msh{width:76px;height:76px}
#app.phone.phshort .phspd .big{font-size:32px}
#app.phone.phtall .phdeck .btn{min-height:70px}
#app.phone #bezel{grid-template-columns:repeat(5,1fr)!important}
#app.phone #page-ship.on{height:100%}
#app.phone .phmain{flex:1;min-height:100%}
#app.phone .phmain>.phdeck:first-of-type{margin-top:auto}
#app.phone .phmap{flex:1;min-height:80px}
#app.phone.phshort .phmap{min-height:70px}

/* ship zones */
#shipg .zband.zmin{fill:rgba(255,179,71,.45)!important;stroke:none!important}#shipg .zband.zmaj{fill:rgba(255,84,104,.5)!important;stroke:none!important}
#shipg .zmin{stroke:#ffb347!important;stroke-width:2.6!important;filter:drop-shadow(0 0 4px #ffb347)}
#shipg .zmaj{stroke:#ff5468!important;stroke-width:3!important;filter:drop-shadow(0 0 5px #ff5468);animation:zp .9s infinite alternate}
@keyframes zp{to{opacity:.55}}
#shipg.hitflash{animation:hit 1.6s infinite}
@keyframes hit{0%,8%{filter:drop-shadow(0 0 10px #ff5468)}20%,100%{filter:none}}
#ann [data-a]{cursor:pointer}
.sys.ping{animation:spg 1.6s}
@keyframes spg{0%,40%{box-shadow:0 0 0 2px #5bd1e6,0 0 18px #5bd1e6}}
.shiplegend{display:flex;justify-content:center;gap:10px;font-size:9px;letter-spacing:.08em;color:var(--dim);margin-top:2px}
.shiplegend i{display:inline-block;width:8px;height:8px;margin-right:4px;vertical-align:-1px}
/* seat states */
.hero>*,.sumrow>*,.sidecol>*{min-width:0}
#app.onfoot .hero{grid-template-columns:1.25fr 1fr}
#app.onfoot .rings.big .ring svg.g{width:62px;height:62px}
#app.onfoot .rings.big{gap:10px 4px}
.lastship .ph .r{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:60%}
.lastship .ls{grid-template-columns:auto 1fr repeat(4,auto)!important;gap:2px 10px}
.lastship .lsname{min-width:0}.lastship .lsname .unit{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
main{overflow-x:hidden}
.figwrap.big{flex-direction:column;align-items:center;text-align:center}
.figwrap.big .figtxt{max-width:84px;font-size:11px}
.lastship .lsname .title{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#app .gunneronly,#app .evaonly{display:none}
#app.gunner .gunneronly{display:block}
#app.gunner .pilotonly{display:none}
#app.eva .evaonly{display:flex}
#app.onfoot .ann{display:none}
#app.morph main{animation:morph .45s}
@keyframes morph{0%{opacity:.25;filter:blur(2px)}100%{opacity:1;filter:none}}
.lastship{opacity:.78;border-style:dashed}
.lastship .ls{display:grid;grid-template-columns:auto 1fr repeat(4,auto);gap:4px 14px;align-items:center}
.lsicon{width:44px;height:36px}
.lsv{display:flex;flex-direction:column;align-items:center}.lsv b{font-size:15px;color:var(--txt)}
.o2hero{gap:16px;align-items:center;border-color:var(--a)!important;background:rgba(255,179,71,.06)!important}
.o2big{font-size:44px;font-weight:700;color:var(--a);line-height:1}
.o2left{font-size:13px;color:#ffe2b8;margin-top:2px}.o2left b{color:#fff}
#app.eva .lastship .ph .r::after{content:" · EVA"}
#app.portrait .lastship .ls{grid-template-columns:auto 1fr repeat(2,auto)}
/* defense v2 */
.defense .defrow{flex:1;min-height:0;display:flex;align-items:stretch}
#app:not(.onfoot) .hero{grid-template-columns:1.6fr 1fr}
#app.portrait .hero,#app.portrait.onfoot .hero{grid-template-columns:1fr!important;grid-template-rows:minmax(260px,1fr) auto}
#app.portrait .sidecol.seat{display:grid;grid-template-columns:1.15fr 1fr;gap:10px}
#app.portrait.onfoot .sidecol.seat{display:none}
#app.portrait .sidecol.foot{display:none}
#app.portrait.onfoot .sidecol.foot{display:grid;grid-template-columns:1fr 1fr;gap:10px}
#app.portrait.onfoot .sidecol.foot .lastship{grid-column:1/3}
#app.portrait.eva .sidecol.foot .lastship{grid-column:1/2}#app.portrait.eva .sidecol.foot .o2hero{grid-column:2/3;grid-row:1}
#app.portrait.onfoot .sidecol.foot>.panel:last-child{grid-column:1/3}
#app.portrait .defense .defrow{min-height:220px}
#app.tablet.portrait .deck.emotes{grid-template-columns:repeat(6,1fr)!important}
.dstat{display:flex;flex-direction:column;align-items:center;gap:4px;min-height:0}
.vbar{flex:1;width:14px;background:#0f2a33;position:relative;min-height:60px;border:1px solid var(--line)}
.vbar i{position:absolute;left:0;right:0;bottom:0;background:var(--c);box-shadow:0 0 8px var(--c);transition:height .4s}
.vbar.g i{background:var(--g);box-shadow:0 0 8px var(--g)}
.dstat .v{font-size:18px;font-weight:700;color:var(--hi)}
.dstat .lab{font-size:9px;letter-spacing:.14em;color:var(--dim)}
.shipbig{width:100%;height:100%;min-height:0}
.sidecol .defense svg.shipbig{max-height:none!important}
/* power chips */
.pchips{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:4px;margin-top:8px}
.pc{font-size:9px;letter-spacing:.08em;padding:4px 0;text-align:center;white-space:nowrap;border:1px solid var(--line);color:var(--mute);border-style:dashed}
.pc.on{border-style:solid}
.pc.on.min{color:#ffb347;border-color:#7a5520;background:rgba(255,179,71,.1)}
.pc.on.maj{color:#ff8a94;border-color:#7a2a33;background:rgba(255,90,106,.12)}
.pc.on{color:var(--g);border-color:#1d5a3b;background:rgba(91,230,154,.08)}
.dguns{padding:7px 9px;background:#0a1418;border:1px solid var(--line);display:flex;flex-direction:column;gap:6px}
.dgl{display:flex;align-items:baseline;gap:6px;font-size:12px;flex-wrap:wrap}.dgl .dgn{font-size:13px;font-weight:600;letter-spacing:.06em;color:var(--hi)}.dgl b{color:var(--g);font-weight:600}
.dgl .dgu{color:var(--mute)}.dgl .dgx{margin-left:auto}
.gdots{display:flex;gap:5px}.gdots i{width:16px;height:8px;border:1px solid var(--line);background:#0f2a33}
.gdots i.rdy{background:var(--g);border-color:var(--g);box-shadow:0 0 5px rgba(91,230,154,.5)}
.gdots i.chg{background:#ffb347;border-color:#ffb347}
.gdots i.off{background:none;border-style:dashed}
.gdots i.nog{opacity:.45}
.dguns.ph{padding:6px 8px}.dguns.ph .dgx{display:none}
.dguns{text-align:left;align-items:stretch}.dguns .dgl,.dguns .gdots{justify-content:flex-start}
.shiplegend span{white-space:nowrap}.shiplegend{column-gap:6px}
#gunmarks .gm{stroke-width:1.2}
#gunmarks .gm.rdy{fill:#5be69a;stroke:#5be69a}
#gunmarks .gm.chg{fill:#ffb347;stroke:#ffb347}
#gunmarks .gm.under{stroke-dasharray:2 1.5}
#gunmarks .gm.off{fill:#06121a;stroke:#7a8a96;stroke-dasharray:2 1.5}
.shiplegend i.lg{border-radius:0;width:7px;height:7px}.shiplegend i.lg.rdy{background:#5be69a}.shiplegend i.lg.chg{background:#ffb347}.shiplegend i.lg.off{background:none;border:1px dashed #7a8a96}
#gunmarks .tm circle{stroke-width:1.4}
#gunmarks .tm.crew circle{fill:#06121a;stroke:#7a8a96}
#gunmarks .tm.rdy circle{fill:#5be69a;stroke:#5be69a}#gunmarks .tm.chg circle{fill:#ffb347;stroke:#ffb347}#gunmarks .tm.off circle{fill:#06121a;stroke:#7a8a96;stroke-dasharray:2 1.5}
#gunmarks .tm .tmr{fill:none!important;stroke:#5bd1e6!important;stroke-dasharray:none!important}
.shiplegend .lgb{display:none}body.bigship .shiplegend .lgb{display:inline-flex}body.bigship .shiplegend .lgs{display:none}
.shiplegend i.lg.tme{border-radius:50%;background:#5be69a;box-shadow:0 0 0 2px #06121a,0 0 0 3px #5bd1e6}.shiplegend i.lg.tcr{border-radius:50%;background:none;border:1px solid #7a8a96}
.dgl.dgship{margin-top:2px}
#wlist .row.tgrp{color:var(--dim)}#wlist .row.tgrp.me{color:var(--hi)}#wlist .row.sub{padding-left:18px}
/* compact combat damage card */
.mapp.fight .dmgp{gap:5px;overflow:hidden}
.drow{grid-template-columns:24px minmax(0,1fr) 84px;gap:8px;padding:5px 8px;cursor:pointer}
.drow .dic{width:24px;height:24px;border:0}.drow .dic svg.ic{width:18px;height:18px}
.drow .dtx{display:flex;align-items:baseline;gap:7px;min-width:0;white-space:nowrap;overflow:hidden}
.drow .dnl{display:inline-flex;align-items:baseline;gap:7px}.drow .dn{font-size:13px;font-weight:600}.drow .dsev{margin-left:0;vertical-align:1px}
.drow .dd{font-size:11px;color:var(--txt);margin:0;overflow:hidden;text-overflow:ellipsis}
.drow .dval{display:flex;flex-direction:column;align-items:flex-end;gap:3px}.drow .dval b{font-size:13px;color:var(--hi);font-weight:600}
.drow .dbar{height:5px;width:100%}
.dmore{font-size:11px;color:var(--a);padding:2px 8px;cursor:pointer}
.dguns{padding:5px 8px;gap:3px}.dgl{align-items:center}.dgl .gdots{display:inline-flex;gap:3px}.gdots i{width:12px;height:7px}
.dfoot{margin-top:auto;display:flex;flex-wrap:wrap;column-gap:14px;row-gap:2px;border-top:1px solid var(--line);padding-top:5px}
.dfoot .dok,.dfoot .dloc{padding:0;border:0;margin:0;font-size:11px}
#app:not(.portrait):not(.phone) .mapp.fight .dmgp{gap:8px}
#app:not(.portrait):not(.phone) .drow{grid-template-columns:34px minmax(0,1fr) 110px;padding:11px 10px}
#app:not(.portrait):not(.phone) .drow .dic{width:34px;height:34px}#app:not(.portrait):not(.phone) .drow .dic svg.ic{width:24px;height:24px}
#app:not(.portrait):not(.phone) .drow .dtx{flex-direction:column;align-items:flex-start;gap:3px}
#app:not(.portrait):not(.phone) .drow .dn{font-size:15px}#app:not(.portrait):not(.phone) .drow .dd{font-size:12px}
#app:not(.portrait):not(.phone) .drow .dval b{font-size:16px}#app:not(.portrait):not(.phone) .drow .dbar{height:7px}
#app:not(.portrait):not(.phone) .dguns{padding:9px 10px}#app:not(.portrait):not(.phone) .dgl .dgn{font-size:14px}
#app:not(.portrait):not(.phone) .gdots i{width:16px;height:9px}
.threat .chip.v{cursor:pointer;flex:none}
.threat .chip.v.r{background:none;color:#ff8a94;border:1px solid var(--r);box-shadow:inset 0 0 8px rgba(255,84,104,.35)}
.threat .chip.v.a{background:none;color:#ffcf8a;border:1px solid var(--a)}
.threat .chip{flex:none}
.threat>span:not(.chip):not(.sep):not(.clock){min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.threat span.long:not(.sep){white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0;flex:0 1 auto}.threat .clock{flex:none}
/* last ship: name readable, stats on the right */
.lastship .ls{display:grid!important;grid-template-columns:minmax(0,1fr) auto!important;gap:4px 12px!important;align-items:center}
.lastship .lsname .title{font-size:16px!important}
.lastship .lsl2{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0}
.lsstats{display:grid;grid-template-columns:repeat(4,auto);gap:2px 10px}
#app.onfoot:not(.portrait) .lsstats{grid-template-columns:repeat(2,auto);gap:0 10px}#app.onfoot:not(.portrait) .lsstats .lsv{flex-direction:row;gap:5px;align-items:baseline}
.lsstats .lsv{display:flex;flex-direction:column;align-items:flex-end}.lsstats .lsv b{font-size:13px}
#app.portrait .lsstats{grid-template-columns:repeat(4,auto)}
/* personal strip */
.ann .pers{cursor:pointer}
/* EVA compact */
#app.eva .o2hero{padding:8px 12px!important;gap:12px}
#app.eva .o2ring svg{width:66px!important;height:66px!important}
#app.eva .o2big{font-size:30px}
#app.eva .o2left{font-size:12px}
.loadcard .lone{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0}
#app.eva .sidecol.foot .rings.big .ring svg.g{width:46px;height:46px}#app.eva .sidecol.foot .rings.big{gap:6px 4px;margin:2px 0}
#app.eva .sidecol.foot .rings.big .ring .val{font-size:12px}
#app.portrait.onfoot .sidecol.foot .rings.big .ring svg.g{width:46px;height:46px}#app.portrait.onfoot .sidecol.foot .rings.big{gap:6px 4px;margin:2px 0}
#gunmarks .tm path{stroke-width:1.4}
#gunmarks .tm.crew path{fill:#06121a;stroke:#7a8a96}
#gunmarks .tm.cand circle:not(.tmr),#gunmarks .tm.cand path{fill:#06121a;stroke:#5bd1e6;stroke-dasharray:2 2}
#gunmarks .tm.under>circle:not(.tmr),#gunmarks .tm.under>path{stroke-dasharray:2 1.5}
#gunmarks .pdc{fill:#7a8a96;opacity:.7}
/* NAV: big map + side column */
#page-nav .navgrid{flex:1}
.navgrid{display:grid;grid-template-columns:minmax(0,1.7fr) minmax(0,1fr);gap:10px;height:100%;min-height:0}
.navmapp{display:flex;flex-direction:column;min-height:0}
.navside{display:flex;flex-direction:column;gap:10px;min-height:0;overflow:auto}
.navmapp .mapwrap{display:flex;flex-direction:column}
.navmapp .mapwrap svg{position:relative!important;inset:auto!important;width:100%!important;flex:1;min-height:0}
.navmapp .mapwrap .dist{position:static!important;width:auto!important;border-left:0!important;border-top:1px solid var(--line)!important;padding:8px 2px 2px!important;display:block!important;column-count:2;column-gap:22px}
.navmapp .mapwrap .dist .dr,.navmapp .mapwrap .dist .dh{break-inside:avoid}.navmapp .mapwrap .dist .dzone{margin-top:6px}
#app.portrait .navgrid{grid-template-columns:1fr;grid-template-rows:minmax(0,1.3fr) auto}
#app.portrait .navside{display:grid;grid-template-columns:1fr 1fr}
#app.phone .navgrid{display:flex!important;flex-direction:column;height:auto;flex:none!important}
#app.phone .navside{display:flex}
#app.phone .navgrid>*,#app.phone .navside>*{min-width:0}
#app.phone .navmapp .ph{flex-wrap:wrap;row-gap:4px}#app.phone .navmapp .ph .r{display:none}
#app.phone .navmapp{flex:none}
#app.phone .navmapp .mapwrap{flex:none;min-height:0}
#app.phone .navmapp .mapwrap svg{flex:none!important;height:auto!important;aspect-ratio:440/420}
#app.phone .navmapp .mapwrap .dist{font-size:11px}
#app.phone #page-nav .kv{flex-wrap:wrap}#app.phone #page-nav .kv b{white-space:normal;text-align:right}
#app.phone #page-nav .ph .r{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:45%}
/* deck: master mode split, operator modes, info tile */
.btn.split{padding:0;flex-direction:row;gap:0;overflow:hidden}
.btn .seg{flex:1;align-self:stretch;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:2px;border:0;background:none;color:var(--dim);font:inherit;font-size:12px;letter-spacing:.12em;cursor:pointer}
.btn .seg small{font-size:9px}#app.phnarrow .btn .seg small{display:none}
.btn .seg.on{background:linear-gradient(#1f6b52,#0e3a2c);color:#d8ffea;box-shadow:inset 0 0 10px rgba(91,230,154,.45)}
.btn .seg+.seg{border-left:1px solid var(--line)}
.btn .seg{position:relative}.btn .seg::after{content:"";position:absolute;inset:-3px -2px}
.btn.opm{grid-column:span 2;padding:4px 4px 0;gap:2px;justify-content:flex-start}
.btn.opm .opl{font-size:9px;letter-spacing:.14em;color:var(--dim)}
.btn.opm .opsegs{display:flex;flex:1;width:100%;border-top:1px solid var(--line)}
.btn.opm .seg{flex-direction:column;font-size:10px}.btn.opm .seg svg.ic{width:16px!important;height:16px!important}
.btn .seg.sent{background:rgba(91,209,230,.25);color:#eafcff}
.btn .bl{white-space:nowrap}
.deck.gdeck .btn{min-height:clamp(70px,12vh,110px)!important}
.deckinfo{display:flex;align-items:center;padding:6px 12px;font-size:11px;color:var(--dim);border:1px dashed var(--line);line-height:1.4}
.phdeck .btn.opm{grid-column:span 2}.phdeck .deckinfo{grid-column:1/-1!important;font-size:10px}
#app.phone .btn .bl{font-size:9px}#app.phone .btn .seg{font-size:10px}
.btn.ro{opacity:.6;cursor:default}
.sys .st.nd{color:var(--mute);border-color:var(--line);border-style:dashed}
.defense svg.shipbig.bubble #fF,.defense svg.shipbig.bubble #fR,.defense svg.shipbig.bubble #fB,.defense svg.shipbig.bubble #fL{stroke-linecap:butt}
.cls{font-size:9px;letter-spacing:.08em;padding:0 4px;border:1px solid;margin-left:4px}.clsH{color:#ff8a94;border-color:#7a2a33}.clsM{color:#ffcf8a;border-color:#7a5520}.clsL{color:#8cc9d6;border-color:#2b6b80}
.ctile.over{border-color:var(--r)}
#suitPanel .kv b{font-size:12px}
/* missions per type */
.msncard .obj{display:block!important;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.msncard .obj .box{display:inline-block;vertical-align:-1px;margin-right:6px}.msncard .mgrid>div:first-child{min-width:0}
.mchips{display:flex;flex-wrap:wrap;gap:4px;align-items:center}.mchips.sm{margin:2px 0 4px}.mchips.sm .mchip,.mchips.sm .mtype{font-size:8px;padding:0 4px}
.mtype{display:inline-flex;align-items:center;gap:4px;font-size:9px;letter-spacing:.12em;color:#06121a;background:#5bd1e6;padding:1px 6px}.mtype svg.ic{width:11px;height:11px}
.mchip{font-size:9px;letter-spacing:.08em;padding:1px 6px;border:1px solid var(--line);color:var(--dim)}.mchip.rep{color:var(--g);border-color:#1d5a3b}.mchip.bp{color:#c9a4ff;border-color:#5a3d7a}
.obj.opt{opacity:.8}.optl{font-size:8px;letter-spacing:.12em;color:#c9a4ff;border:1px solid #5a3d7a;padding:0 4px;margin-right:4px}
.mdet{display:grid;grid-template-columns:minmax(0,1.2fr) minmax(0,1fr);gap:14px;align-items:start}
#app.phone .mdet,#app.portrait .mdet{grid-template-columns:1fr}
.mprog{border:1px solid var(--line);background:#0a1418;padding:8px 10px;display:flex;flex-direction:column;gap:5px}
.mph{font-size:9px;letter-spacing:.14em;color:var(--dim)}
.mdest{display:flex;align-items:center;gap:5px;font-size:11px;color:var(--hi);margin-top:3px}
.mdrop{display:grid;grid-template-columns:minmax(0,1fr) 70px auto;gap:8px;align-items:center;font-size:11px}.mdrop span:first-child{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.mdrop.done{color:var(--g)}.mdrop b{font-weight:600;white-space:nowrap}
.mbar{display:inline-block;height:5px;background:#0f2a33;position:relative;width:100%}.mbar i{position:absolute;inset:0 auto 0 0;background:#5bd1e6}.mdrop.done .mbar i{background:var(--g)}
.mstat{display:flex;align-items:baseline;gap:8px;font-size:11px;color:var(--dim)}.mstat b{font-size:20px;color:var(--hi)}
.mrow .mmet{display:flex;flex-direction:column;align-items:flex-end;gap:3px;font-size:11px;color:var(--hi);white-space:nowrap;min-width:70px}.mrow .mmet .mbar{width:70px}
.mrow{grid-template-columns:auto minmax(0,1fr) auto auto auto!important}
#app.phone .mrow{grid-template-columns:auto minmax(0,1fr) auto auto!important}#app.phone .mrow .mwh{display:none}
.mrow .mtx b svg.ic{width:12px;height:12px;vertical-align:-2px}
/* feed per notification type */
.ev{border-left:2px solid transparent;padding-left:6px}
.ev.g-alert{border-left-color:#ff5468}.ev.g-alert svg.ic{color:#ff8a94}
.ev.g-msn{border-left-color:#2b6b80}.ev.g-money{border-left-color:#1d5a3b}.ev.g-money svg.ic{color:#5be69a}
.ev.g-people{border-left-color:#3d5a8a}.ev.g-people svg.ic{color:#8fb4ff}.ev.g-zone{opacity:.7}.ev.g-ship svg.ic{color:#7aa3ae}
.ev.bp{border-left-color:#7a5aa8}.ev.bp svg.ic{color:#c9a4ff}
.ev .evt{display:flex;flex-direction:column;min-width:0}.ev .evs{font-size:10px;color:var(--dim)}
#filters button i{font-style:normal;font-size:9px;margin-left:5px;color:var(--fc,var(--dim))}
#filters button{border-bottom:2px solid var(--fc,transparent)}
.need .nwhat{color:var(--dim);font-size:11px}
.need.done{opacity:.55}
/* alarm overlay: whole interface */
#app.sdalarm{box-shadow:0 0 0 3px #ff2d43,0 0 60px rgba(255,45,67,.6)!important}
#app.sdalarm::after{content:"";position:absolute;inset:0;pointer-events:none;z-index:40;background:radial-gradient(ellipse at center,rgba(255,45,67,.14) 20%,rgba(255,45,67,.5) 100%);box-shadow:inset 0 0 0 4px rgba(255,45,67,.9),inset 0 0 80px rgba(255,45,67,.55);animation:sdpulse 1s infinite}
#app.sdfinal::after{animation-duration:.4s}
#app.sdboom::after{background:rgba(255,45,67,.45);animation:none}
@keyframes sdpulse{0%,100%{opacity:.45}50%{opacity:1}}
#app.sdalarm .panel{border-color:rgba(255,84,104,.7)!important;background-color:#1a0a10!important}
#app.sdalarm .osb,#app.sdalarm .phtab{border-color:#ff2d43!important}
#app.sdalarm .threat{background:#4a0d16!important;border-color:#ff2d43!important}
#sdBanner{position:absolute;left:50%;top:44px;transform:translateX(-50%);z-index:45;display:flex;align-items:center;gap:16px;padding:8px 18px;background:rgba(40,4,10,.94);border:2px solid #ff2d43;box-shadow:0 0 30px rgba(255,45,67,.7);color:#fff}
#sdBanner .sdl,#sdBanner .sdh{white-space:nowrap}
#sdBanner .sdl{font-size:11px;letter-spacing:.18em;color:#ff8a94}
#sdBanner .sdt{font-size:40px;font-weight:700;letter-spacing:.04em;font-variant-numeric:tabular-nums;line-height:1}
#sdBanner .sdh{font-size:12px;color:#ffd3d8}
#sdBanner .sdc{font:inherit;font-size:12px;letter-spacing:.14em;padding:10px 18px;min-height:40px;background:#ff2d43;color:#fff;border:0;cursor:pointer}
#app.sdfinal #sdBanner .sdt{animation:sdblink .5s steps(2) infinite}
@keyframes sdblink{50%{opacity:.25}}
.btn.sdbtn{background:linear-gradient(#a3122a,#5a0812)!important;border-color:#ff2d43!important;color:#fff!important;animation:sdpulse 1s infinite}
.btn.sdbtn small{color:#fff!important;font-size:12px!important;font-weight:700}
#app.phone #sdBanner{top:34px;width:calc(100% - 20px);justify-content:space-between;padding:6px 10px;gap:8px}
#app.phone #sdBanner .sdt{font-size:30px}#app.phone #sdBanner .sdh{display:none}
@media (prefers-reduced-motion:reduce){#app.sdalarm::after,.btn.sdbtn,#app.sdfinal #sdBanner .sdt{animation:none}}
/* touch: small header controls get a bigger invisible hit area */
.sysbtn,.mapmodes button,.mapbtn,.phmhead button,.ph .r[data-go]{position:relative}
.sysbtn::after,.mapmodes button::after,.mapbtn::after,.phmhead button::after,.ph .r[data-go]::after{content:"";position:absolute;inset:-8px -3px}
.ph .r[data-go]::after{inset:-11px -4px}
/* no right rail: notifications live behind the bell, full list on FEED */
#app .rail{display:none!important}
#app,#app.tablet,#app.tablet.feedpage,#app.feedpage{grid-template-columns:72px 1fr}
#app.phone,#app.phone.feedpage{grid-template-columns:1fr}
#app .threat{grid-column:1/-1!important}
.bell{flex:none;position:relative;display:flex;align-items:center;justify-content:center;gap:5px;min-width:36px;height:24px;padding:0 7px!important;margin-left:8px;background:none;border:1px solid var(--line);color:var(--c);cursor:pointer;padding:0}
.bell svg{width:16px;height:16px;fill:none;stroke:currentColor;stroke-width:1.6}
.bell::after{content:"";position:absolute;inset:-8px -4px}
.bell .bb{position:static;min-width:17px;height:17px;padding:0 4px;border-radius:9px;background:#ffb347;color:#06121a;font-size:10px;font-weight:700;line-height:17px;text-align:center;font-style:normal;letter-spacing:0}
.bell.open{background:#123744;border-color:var(--c2)}
/* SCREEN + bell: one group at the right end of the strip */
.threat #scrBtn{margin-left:auto}
.threat .clock+#scrBtn{margin-left:10px}
.threat #bellBtn{margin-left:6px;margin-right:0}
#bellPanel{position:absolute;top:48px;right:14px;width:390px;max-height:calc(100% - 64px);overflow:auto;z-index:50;background:rgba(6,18,26,.98);border:1px solid var(--c2);box-shadow:0 14px 44px #000;padding:10px 12px}
#bellPanel .need{margin-bottom:6px}
#bellPanel .bellall{display:block;width:100%;margin-top:10px;min-height:34px}
#toasts{position:absolute;top:48px;right:14px;width:350px;z-index:48;display:flex;flex-direction:column;gap:6px;pointer-events:none}
.toast{pointer-events:auto;cursor:pointer;display:flex;gap:9px;align-items:center;padding:8px 11px;background:rgba(8,25,35,.97);border:1px solid var(--line);border-left:3px solid var(--tc,#5bd1e6);box-shadow:0 8px 26px #000;font-size:12px;color:var(--hi);animation:tin .25s ease-out}
.toast .tl{font-size:9px;letter-spacing:.14em;color:var(--tc,#9fd2de)}
.toast.ans{--tc:#ffb347;background:rgba(40,28,8,.97)}
.toast.out{animation:tout .3s forwards}
@keyframes tin{from{transform:translateX(30px);opacity:0}}@keyframes tout{to{transform:translateX(30px);opacity:0}}
#toasts.instrip{flex-direction:row;justify-content:flex-end;gap:0}
#toasts.instrip .toast{height:100%;width:100%;padding:0 10px;box-shadow:none;border-top:0;border-bottom:0;border-right:0;gap:8px;white-space:nowrap;overflow:hidden}
#toasts.instrip .toast>div{display:flex;gap:8px;align-items:baseline;min-width:0;overflow:hidden;text-overflow:ellipsis}
#toasts.instrip .toast .tl{flex:none}
#app.phone #toasts,#app.phone #bellPanel{left:8px;right:8px;width:auto;top:34px}
#app.sdalarm #toasts{top:122px}
@media (prefers-reduced-motion:reduce){.toast,.toast.out{animation:none}}
/* MAIN landscape: map | defense | flight, each full height */
#app:not(.onfoot):not(.portrait):not(.phone) .hero{grid-template-columns:minmax(0,1.25fr) minmax(0,2fr)}
#app:not(.onfoot):not(.portrait):not(.phone) .sidecol.seat{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:10px}
#app:not(.onfoot):not(.portrait):not(.phone) .sidecol.seat>.panel{min-height:0}
/* flight instruments */
.flight .frow .vsi{font-size:13px;color:var(--hi);font-weight:700}.flight .frow .vsi.dn{color:#ffb347}.flight .frow .vsi.na{color:var(--mute);font-weight:400}
.hdg{position:relative;height:30px;margin:6px 0 4px;border:1px solid var(--line);background:#081820;overflow:hidden}
.hdg span{position:absolute;top:4px;transform:translateX(-50%);font-size:10px;color:var(--dim);letter-spacing:.06em}
.hdg span.c{color:#ffcf8a}
.hdg i{position:absolute;bottom:0;width:1px;height:6px;background:#2b6b80}
.hdg b{position:absolute;left:50%;top:0;bottom:0;width:46px;transform:translateX(-50%);border:1px solid var(--c);background:rgba(8,24,32,.95);color:var(--hi);font-size:13px;display:flex;align-items:center;justify-content:center}
.fgrid{display:grid;grid-template-columns:1fr 1fr;gap:0 14px}
.fgrid .kv{margin:5px 0 2px}
.flight .ph.sub{margin:10px 0 0;font-size:9px}
.flight .pchips{grid-template-columns:repeat(3,minmax(0,1fr));margin-top:5px}
.pc.en{border-style:solid;color:#8cc9d6;border-color:#2b6b80}
.setgrid{display:grid;grid-template-columns:1fr 1fr;gap:4px 24px}
.setgrid .sh{grid-column:1/-1;font-size:9px;letter-spacing:.16em;color:var(--dim);margin-top:6px}
.setgrid .sr{display:flex;justify-content:space-between;font-size:12px;color:var(--txt);padding:3px 0;border-bottom:1px solid rgba(31,76,94,.35)}
.setgrid .sr b{font-weight:600;color:var(--mute)}.setgrid .sr b.y{color:var(--g)}
#app.phone .setgrid{grid-template-columns:1fr}
/* SYS landscape: power, countermeasures, weapons | systems, settings */
#app:not(.portrait):not(.phone) #page-sys.on{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.35fr);grid-template-rows:auto auto 1fr;align-content:start;gap:10px;overflow:auto}
#app:not(.portrait):not(.phone) #page-sys>.two{grid-column:1;grid-row:1;grid-template-columns:1fr!important}
#app:not(.portrait):not(.phone) #page-sys>.sysweap{grid-column:1;grid-row:2}
#app:not(.portrait):not(.phone) #page-sys>.syspanel{grid-column:2;grid-row:1/4}
#app:not(.portrait):not(.phone) #page-sys>.setpanel{grid-column:1;grid-row:3}
#wlist .row{flex-wrap:wrap;row-gap:4px}#wlist .row .r{margin-left:auto}#wlist .gdots{flex-wrap:wrap;min-width:0}
#app:not(.portrait):not(.phone).gunner #page-sys>.syspanel{grid-row:1/4}
#app:not(.portrait):not(.phone) #page-sys .sysgrid{grid-template-columns:repeat(2,minmax(0,1fr))!important}
#wlist .gdots{margin-left:8px}
/* MSN landscape: contracts + session | pinned detail */
#app:not(.portrait):not(.phone) #page-msn.on{display:grid;grid-template-columns:minmax(0,1.2fr) minmax(0,1fr);grid-template-rows:auto 1fr;gap:10px;overflow:auto;align-content:start}
#app:not(.portrait):not(.phone) #page-msn>#msnList{grid-column:1;grid-row:1}
#app:not(.portrait):not(.phone) #page-msn>#msnHist{grid-column:1;grid-row:2;align-self:start}
#app:not(.portrait):not(.phone) #page-msn>#msnPinned{grid-column:2;grid-row:1/3;align-self:start}
/* FEED landscape: answer in game | full notification list */
#app:not(.portrait):not(.phone) #page-feed.on{display:grid;grid-template-columns:minmax(300px,.8fr) minmax(0,1.6fr);gap:10px}
#app:not(.portrait):not(.phone) #page-feed>.panel:first-child{grid-column:1;align-self:start;max-height:100%;overflow:auto}
#app:not(.portrait):not(.phone) #page-feed>.panel:nth-child(2){grid-column:2;grid-row:1;min-height:0}
#app:not(.portrait):not(.phone) .sessgrid{grid-template-columns:repeat(8,minmax(0,1fr))!important}
#app:not(.portrait):not(.phone) #page-feed>.panel:first-child .ph .r{display:none}
/* MAIN landscape, seated: damage | defense | flight, then mission | you & outside, flight runs full height */
#app:not(.onfoot) .sumrow>.oldyou{display:none!important}
#app.onfoot .envcard{display:none!important}
.spdo{position:relative;margin:0 auto;width:100%;max-width:230px}
.spdl{display:flex;justify-content:center;gap:12px;font-size:10px;letter-spacing:.06em;margin-top:2px}
.spdo svg{display:block;width:100%;height:auto}
.spdc{position:absolute;left:0;right:0;top:52%;transform:translateY(-50%);text-align:center;display:flex;flex-direction:column;align-items:center;gap:0}
.spdc .big{font-size:34px;line-height:1}.spdc .vsi{font-size:12px;margin-top:2px}
.fstate,.shipstate{display:flex;flex-wrap:wrap;gap:4px;margin:6px 0 2px}
.fs{font-size:9px;letter-spacing:.1em;padding:3px 7px;border:1px solid var(--line);color:var(--dim)}
.fs.on{color:var(--g);border-color:#1d5a3b;background:rgba(91,230,154,.08)}.fs.mm{color:var(--c);border-color:var(--c2);background:#0f2f3a}
.shipid{margin:-4px 0 2px;letter-spacing:.06em}
.shipstate{justify-content:center;margin:4px 0}
.envcard{display:flex;flex-direction:column;gap:6px}
.envv{font-size:12px;font-weight:700;letter-spacing:.04em;padding:6px 9px;border:1px solid}
.envv.ok{color:var(--g);border-color:#1d5a3b;background:rgba(91,230,154,.08)}
.envv.warn{color:#ffcf8a;border-color:#7a5520;background:rgba(255,179,71,.1)}
.envv.bad{color:#ff8a94;border-color:#7a2a33;background:rgba(255,90,106,.12)}
.envg{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:4px 10px}
.envg .ek{display:flex;flex-direction:column;min-width:0}.envg .ek b{font-size:13px;color:var(--hi);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.envg b.ok{color:var(--g)}.envg b.warn{color:#ffcf8a}.envg b.bad{color:#ff8a94}
.envvit{display:flex;flex-wrap:wrap;gap:4px 12px;font-size:12px;margin-top:auto;padding-top:4px;border-top:1px solid rgba(31,76,94,.4)}.envvit b{font-weight:700}
.envcard .rings.small{display:grid;grid-template-columns:repeat(2,auto);gap:2px 8px;margin:0}
.envcard .ring svg.g{width:34px;height:34px}.envcard .ring .val{font-size:11px}.envcard .ring .lab{display:none}
.envg b{font-size:12px!important}
/* MAIN portrait, seated: damage over defense | flight full height, then mission | you & outside */
.phrow.env.bad{border-color:#7a2a33}.phrow.env.bad b{color:#ff8a94}.phrow.env.warn b{color:#ffcf8a}.phrow.env.ok b{color:var(--g)}
/* mission card */
.mnum{display:inline-grid;place-items:center;width:22px;height:22px;border-radius:50%;border:1.5px solid #c8913f;color:#ffcf8a;font-size:11px;flex:none;background:#0a1a22}
.mnum.s{width:16px;height:16px;font-size:9px;border-width:1px}
.dr b{white-space:nowrap}.dr.dm{cursor:pointer}.dr.dm .dmn{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.dr.dm.on{color:#ffcf8a}.dr.dm.on .mnum{background:#c8913f;color:#06121a}
.mrow{display:grid;grid-template-columns:auto minmax(0,1fr) auto auto;gap:10px;align-items:center;width:100%;text-align:left;padding:9px 10px;margin-top:6px;background:#0a1418;border:1px solid var(--line);color:inherit;font:inherit;cursor:pointer}
.mrow.on{border-color:#c8913f;background:rgba(200,145,63,.08)}.mrow.on .mnum{background:#c8913f;color:#06121a}
.mrow .mtx{display:flex;flex-direction:column;min-width:0}.mrow .mtx b{color:var(--hi);font-weight:600;font-size:13px}
.mrow .mtx span{font-size:11px;color:var(--dim);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.mrow .mwh{font-size:11px;color:var(--dim);white-space:nowrap}
.mrow .mpinb{font-size:9px;letter-spacing:.14em;padding:3px 8px;border:1px solid var(--line);color:var(--dim)}.mrow.on .mpinb{color:#ffcf8a;border-color:#c8913f}
#app.phone .mrow{grid-template-columns:auto minmax(0,1fr) auto}#app.phone .mrow .mwh{display:none}

.sumrow{grid-template-columns:1.45fr 1fr!important}
.msncard .mgrid{display:grid;grid-template-columns:1fr auto;gap:12px}
.msncard .title{margin-bottom:2px}
.msncard .obj{padding:2px 0;font-size:12px}
.mside{display:grid;grid-template-columns:1fr 1fr;gap:4px 14px;align-content:start;border-left:1px solid var(--line);padding-left:12px}
.mside b{font-size:14px;color:var(--hi);font-weight:600}
/* you card + figure */
.youcard{display:grid;grid-template-columns:1fr auto;gap:8px;align-items:center}
.figwrap{display:flex;align-items:center;gap:6px}
.figwrap svg.fig{width:34px;height:58px}
.figwrap.big svg.fig{width:60px;height:104px}
.figtxt{font-size:10px;color:#ffd39a;line-height:1.35;max-width:90px}
.figwrap.big .figtxt{font-size:12px;max-width:120px}
.rings.small .ring svg.g{width:46px;height:46px}
/* feed rail fills */
.rail .feedbox{overflow:hidden}
.sysgrid{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}
.sys{border:1px solid var(--line);background:rgba(8,25,35,.8);padding:8px 10px;display:flex;flex-direction:column;gap:3px}
.sys .top{display:flex;align-items:center;gap:6px;font-size:11px;letter-spacing:.1em;color:var(--hi)}
.sys .top .nm{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
#app.phone .sys .top{flex-wrap:wrap;row-gap:3px}#app.phone .sys .top .nm{flex:1 1 calc(100% - 22px)}#app.phone .sys .top .st{margin-left:0}
.sys .st{flex-shrink:0;margin-left:auto;font-size:9px;letter-spacing:.1em;padding:1px 6px;border:1px solid}
.sys .st.ok{color:var(--g);border-color:#1d5a3b}.sys .st.min{color:#000;background:var(--a);border-color:var(--a)}.sys .st.maj{color:#fff;background:var(--r);border-color:var(--r)}.sys .st.off{color:var(--mute);border-color:var(--line)}
.sys .part{font-size:10px;color:var(--dim)}
.sys.warnmaj{border-color:var(--r);box-shadow:inset 0 0 10px rgba(255,84,104,.25)}
.sys.warnmin{border-color:var(--a)}
#app.portrait .sysgrid{grid-template-columns:repeat(2,1fr)}
#app.phone .sysgrid{grid-template-columns:repeat(2,minmax(0,1fr))!important}.sys{min-width:0}.sys .top{min-width:0;white-space:nowrap;overflow:hidden}.sys .part{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#app.phone #page-sys .two,#app.phone #page-sys [style*='grid-template-columns']{grid-template-columns:1fr!important}#app.phone #page-sys>*{min-width:0}
#app.tablet.portrait .bezel,#app.phone .bezel,#app.portrait .bezel{grid-template-columns:repeat(7,1fr)!important}
#app.tablet.portrait .osb{flex-direction:row;justify-content:center;gap:6px;font-size:11px;padding:10px 0}
.bar{height:8px;background:#0f2a33;position:relative;overflow:hidden}
.bar i{position:absolute;inset:0 auto 0 0;background:var(--c);box-shadow:0 0 6px var(--c)}
.bar.a i{background:var(--a);box-shadow:none}.bar.r i{background:var(--r)}.bar.g i{background:var(--g)}
.kv{display:flex;align-items:baseline;justify-content:space-between;margin:9px 0 4px;font-size:11px;color:var(--dim);letter-spacing:.06em}
.kv b{font-weight:600;color:var(--hi);font-size:13px;letter-spacing:0}
.def{display:grid;grid-template-columns:1fr;gap:4px}
.pips{display:flex;justify-content:space-around;align-items:flex-end;height:96px;padding-top:4px}
.pip{display:flex;flex-direction:column-reverse;gap:3px;align-items:center}
.pip i{display:block;width:18px;height:8px;background:#0f2a33;border:1px solid var(--line)}
.pip i.f{background:var(--c);border-color:var(--c);box-shadow:0 0 5px var(--c)}
.pip i.off{opacity:.25}
.pip span{font-size:10px;color:var(--dim);margin-top:4px;letter-spacing:.08em}

/* deck */
.deck{display:grid;grid-template-columns:repeat(6,1fr);gap:8px}
.btn{position:relative;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:4px;min-height:62px;font-size:10px;letter-spacing:.1em;color:#a8e6f2;background:linear-gradient(#0f3340,#081f27);border:1px solid #2b6b80;transition:transform .06s}
.btn:active{transform:scale(.97)}
.btn small{font-size:9px;letter-spacing:.02em;color:var(--dim)}
.btn.lit{background:linear-gradient(#1f6b52,#0e3a2c);border-color:var(--g);color:#d8ffea;box-shadow:inset 0 0 12px rgba(91,230,154,.45)}
.btn.lit small{color:#9fe8c4}
.btn.guard{background:repeating-linear-gradient(45deg,#3a2a08 0 8px,#1a1404 8px 16px);border-color:var(--a);color:var(--a)}
.btn.armed{background:linear-gradient(#6b1f2a,#3a0e14);border-color:var(--r);color:#fff;box-shadow:0 0 14px var(--r);animation:pulse .8s infinite alternate}
@keyframes pulse{to{box-shadow:0 0 4px var(--r)}}
.btn.flash{animation:flash .35s}
@keyframes flash{50%{background:#5bd1e6;color:#000}}

/* feed rail */
.rail{display:flex;flex-direction:column;gap:10px;min-height:0;overflow:hidden}
.need{display:flex;align-items:center;gap:8px;padding:8px 10px;border-left:3px solid var(--a);background:rgba(255,179,71,.08);color:#ffe2b8;margin-bottom:6px;cursor:pointer}
.need .who{font-size:11px;color:#c9a97a}
.need .t{margin-left:auto;font-size:10px;color:#c9a97a}
.need.done{opacity:.35;border-left-color:var(--mute);text-decoration:line-through}
.ev{display:flex;align-items:center;gap:8px;padding:5px 0;border-bottom:1px solid rgba(31,76,94,.4);font-size:12px;color:#9fd2de}
.ev .t{width:38px;flex:none;color:var(--mute);font-size:11px}
.ev.new{animation:newev 1.4s}
@keyframes newev{from{background:rgba(91,209,230,.25)}}
.filters{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:8px}
.filters button{font-size:10px;letter-spacing:.1em;padding:4px 12px;min-height:34px;border:1px solid var(--line);color:var(--dim)}
.filters button.on{background:#123744;color:var(--c);border-color:var(--c2)}

/* missions */
.obj{display:flex;align-items:center;gap:8px;padding:6px 0;font-size:13px}
.obj .box{width:14px;height:14px;border:1px solid var(--c);flex:none}
.obj.done{color:var(--mute)}.obj.done .box{background:var(--g);border-color:var(--g)}
.obj.cur{color:var(--hi)}.obj.cur .box{box-shadow:0 0 6px var(--c)}
.tag{font-size:10px;letter-spacing:.08em;padding:2px 6px;border:1px solid var(--line);color:var(--dim)}
.list .row{display:flex;align-items:center;gap:8px;padding:7px 0;border-bottom:1px solid rgba(31,76,94,.4)}
.list .row .r:not(.dot){margin-left:auto;color:var(--dim);font-size:12px}

/* people */
.dot{width:8px;height:8px;border-radius:50%;background:var(--g);flex:none}.dot.c{background:#5bd1e6}.dot.a{background:var(--a)}.dot.m{background:var(--mute)}.dot.r{background:var(--r)}

/* you */
.vit{display:grid;grid-template-columns:repeat(2,1fr);gap:10px 18px}
.body svg path{fill:#0f2a33;stroke:var(--line)}
.body svg .hurt{fill:rgba(255,179,71,.55);stroke:var(--a)}

/* ---------- phone / portrait ---------- */
#app.phone,#app.portrait{grid-template-columns:1fr;grid-template-rows:auto auto 1fr auto;padding:8px}
#app.phone .threat,#app.portrait .threat{grid-column:1;font-size:11px;gap:6px;overflow:hidden;white-space:nowrap}
#app.phone .threat .long,#app.portrait .threat .long{display:none}
#app.phone .demo,#app.portrait .demo{margin-left:auto}
#app.phone .threat .clock,#app.portrait .threat .clock{display:none}
#app.phone .rail,#app.portrait .rail{grid-row:2;order:0;margin-bottom:8px}
#app.phone .rail .feedbox,#app.portrait .rail .feedbox{display:none}
#app.phone .rail .needbox .ph,#app.portrait .rail .needbox .ph{display:none}
#app.phone .rail .needbox,#app.portrait .rail .needbox{padding:0;border:0;background:none}
#app.phone .rail .need:nth-child(n+4),#app.portrait .rail .need:nth-child(n+4){display:none}
#app.phone main,#app.portrait main{grid-row:3}
#app.phone .bezel,#app.portrait .bezel{grid-row:4;display:grid;grid-template-columns:repeat(6,1fr);gap:5px;margin-top:8px}
#app.phone .osb,#app.portrait .osb{padding:7px 0 5px;font-size:9px}
#app.phone .shipgrid,#app.portrait .shipgrid{grid-template-columns:1fr 1fr}
#app.phone .shipgrid .pwr,#app.portrait .shipgrid .pwr{display:none}
#app.phone .shipgrid .defense,#app.portrait .shipgrid .defense{grid-row:1;grid-column:1/3}
#app.phone .ann,#app.portrait .ann{grid-template-columns:repeat(5,1fr)}
#app.phone .deck,#app.portrait .deck{grid-template-columns:repeat(4,1fr)}
#app.phone .big,#app.portrait .big{font-size:32px}
#app.phone .vit,#app.portrait .vit{grid-template-columns:1fr}
#app.phone .two,#app.portrait .two{grid-template-columns:1fr !important}
#app.phone #page-feed.on ~ * {}
.hideonrail{}
#app.feedpage .rail{display:none}
#app.feedpage{grid-template-columns:76px 1fr}
#app.feedpage .threat{grid-column:1/3}
#app.phone.feedpage,#app.portrait.feedpage{grid-template-columns:1fr}

/* ===== MAIN, seated (tablet + desktop, both rotations): map | ship + damage | instrument cluster ===== */
#app:not(.phone):not(.onfoot) #page-ship.on{display:grid!important;grid-template-columns:minmax(0,1.4fr) minmax(0,1fr) 300px;grid-template-rows:minmax(0,1fr) auto auto;grid-template-areas:"dmg def fly" "msn you fly" "deck deck fly";gap:12px}
#app.portrait:not(.phone):not(.onfoot) #page-ship.on{grid-template-columns:minmax(0,1fr) 300px;grid-template-rows:minmax(0,1.15fr) minmax(0,1fr) auto auto auto;grid-template-areas:"dmg fly" "def fly" "msn fly" "you fly" "deck deck"}
#app:not(.phone):not(.onfoot) #page-ship .hero,#app:not(.phone):not(.onfoot) #page-ship .sidecol.seat,#app:not(.phone):not(.onfoot) #page-ship .sumrow{display:contents!important}
#app:not(.phone):not(.onfoot) #page-ship .ann{display:none!important}
#app:not(.phone):not(.onfoot) #page-ship .mapp{grid-area:dmg}
#app:not(.phone):not(.onfoot) #page-ship .defense{grid-area:def}
#app:not(.phone):not(.onfoot) #page-ship .flight,#app:not(.phone):not(.onfoot) #page-ship .gunneronly{grid-area:fly}
#app:not(.phone):not(.onfoot) #page-ship .msncard{grid-area:msn}
#app:not(.phone):not(.onfoot) #page-ship .envcard{display:none!important}
#app:not(.phone):not(.onfoot) #page-ship .sumrow>.oldyou{display:block!important;grid-area:you}
#app:not(.phone):not(.onfoot):not(.portrait) #page-ship .oldyou .youcard{grid-template-columns:1fr;gap:4px}
#app:not(.phone):not(.onfoot):not(.portrait) #page-ship .oldyou .figwrap{justify-content:flex-start}
#app:not(.phone):not(.onfoot):not(.portrait) #page-ship .oldyou .figwrap svg.fig{width:16px;height:28px}
#app:not(.phone):not(.onfoot):not(.portrait) #page-ship .oldyou .figtxt{max-width:none;display:flex;flex-wrap:wrap;gap:0 10px}
#app:not(.phone):not(.onfoot) #page-ship .deck.seat{grid-area:deck}
#app:not(.phone):not(.onfoot):not(.gunner) #page-ship .flight{padding:6px 4px!important;display:flex}
#app:not(.phone):not(.onfoot) #page-ship .flight>*:not(#hudsvg){display:none!important}
#hudsvg{display:block;width:100%;height:100%;min-height:0}
#app.portrait:not(.phone) #page-ship .defense .defrow{min-height:0!important}
.page.on{padding:5px}
#app.phone .page.on{padding:3px 2px}
#app.phnarrow .btn .seg{font-size:9px!important;letter-spacing:.02em!important;padding-left:1px!important;padding-right:1px!important}
.mapp .mapwrap{overflow:hidden}
.dhandle{display:none}
.mapp .mapwrap.narrow svg{width:100%!important}
.mapp .mapwrap.narrow .dist{transform:translateX(calc(100% + 2px));transition:transform .25s ease;background:rgba(4,18,26,.97)!important;border-left:1px solid var(--c2)!important;box-shadow:-10px 0 24px rgba(0,0,0,.7);padding:6px 8px 6px 12px!important;z-index:4}
.mapp .mapwrap.narrow.open .dist{transform:none}
.mapp .mapwrap.narrow .dhandle{display:flex;position:absolute;right:0;top:50%;transform:translateY(-50%);z-index:5;writing-mode:vertical-rl;align-items:center;gap:6px;padding:10px 4px;font:inherit;font-size:9px;letter-spacing:.2em;color:var(--c);cursor:pointer;
 border:1px solid var(--c2);border-right:0;border-radius:4px 0 0 4px;background:linear-gradient(90deg,#123744,#0a2129);box-shadow:-3px 0 8px rgba(0,0,0,.5);transition:right .25s ease}
.mapp .mapwrap.narrow .dhandle::after{content:"";position:absolute;inset:-6px -4px -6px -10px}
.mapp .mapwrap.narrow.open .dhandle{right:156px}
@media (prefers-reduced-motion:reduce){.mapp .mapwrap.narrow .dist,.mapp .mapwrap.narrow .dhandle{transition:none}}
#app.phone #hudsvg{display:none}
#app:not(.phone) .shipid,#app:not(.phone) .shipstate,#app:not(.phone) .shiplegend{display:none!important}
#app:not(.phone) .envcard .envv,#app:not(.phone) .envcard .envg{display:none!important}
.envinj{font-size:11px;color:#ffcf8a}
#app:not(.incombat) #page-ship .dlist{display:none!important}
.dlist{display:flex;flex-direction:column;gap:4px;margin-top:6px}
.dr2{border-left:3px solid var(--line);padding:3px 8px;font-size:12px;display:flex;justify-content:space-between;align-items:center;background:rgba(0,0,0,.25);color:var(--dim)}
.dr2 b{color:var(--hi)}.dr2.maj{border-color:var(--r);color:#ff8a94}.dr2.maj b{color:#ff8a94}.dr2.min{border-color:var(--a);color:#ffcf8a}.dr2.min b{color:#ffcf8a}.dr2.ok{border-color:#1d5a3b;color:var(--g)}.dr2.nd{color:var(--mute);font-size:11px}.dr2.nd b{color:var(--mute);font-weight:400}
.dr2 .gdots{display:inline-flex;margin-left:6px;vertical-align:middle}.dr2 .gdots i{width:12px;height:7px}

#app.tablet,#app.tablet.feedpage,#app.feedpage,#app:not(.phone){grid-template-columns:58px 1fr}
#app.portrait,#app.portrait.feedpage{grid-template-columns:1fr}
#app:not(.phone):not(.portrait) .osb{font-size:9px!important;letter-spacing:.1em!important;padding:16px 2px 7px!important}
#app:not(.phone) .btn:not(.split):not(.opm){padding-top:12px!important}
#app.phone .btn:not(.split):not(.opm){padding-top:9px!important}
.osb::after,.btn:not(.split):not(.opm)::after{top:5px!important}
/* debossed CSR mark + tablet name, bottom-left corner under the rail */
.brand{position:absolute;left:12px;bottom:16px;width:62px;display:flex;flex-direction:column;align-items:center;gap:5px;pointer-events:none;z-index:1}
.brand svg{width:34px;height:42px;filter:drop-shadow(0 -1px 0 rgba(0,0,0,.85)) drop-shadow(0 1px 0 rgba(255,255,255,.09));opacity:.78}
.brand b{font-size:10px;letter-spacing:.26em;color:#46545a;text-shadow:0 -1px 0 #000,0 1px 0 rgba(255,255,255,.07);font-weight:700}
.brand i{font-style:normal;font-size:6.5px;letter-spacing:.2em;color:#3a464b;text-shadow:0 -1px 0 #000,0 1px 0 rgba(255,255,255,.05);white-space:nowrap}
#app.phone .brand,#app.portrait .brand{display:none}
#app:not(.phone) .threat .long{display:none!important}
#ticker{position:absolute;z-index:6;overflow:hidden;cursor:pointer;border-radius:2px;background:linear-gradient(90deg,rgba(0,0,0,.35),rgba(0,0,0,.15));box-shadow:inset 0 0 0 1px rgba(91,209,230,.12)}
#ticker .tki{display:flex;align-items:center;gap:8px;height:100%;padding:0 10px;white-space:nowrap;font-size:11px;color:#d6eef3;border-left:3px solid var(--tc)}
#ticker .tki.in{animation:tkin .35s ease-out}
#ticker .tki.new{background:linear-gradient(90deg,color-mix(in srgb,var(--tc) 22%,transparent),transparent 70%)}
#ticker .tl{font-size:9px;letter-spacing:.16em;color:var(--tc);font-weight:700;flex:none}
#ticker .tt{overflow:hidden;text-overflow:ellipsis;min-width:0}
#ticker .ta{margin-left:auto;flex:none;font-size:10px;color:var(--mute);padding-left:8px}
#ticker .ic{flex:none;color:var(--tc)}
@keyframes tkin{from{transform:translateY(100%);opacity:0}}
@media (prefers-reduced-motion:reduce){#ticker .tki.in{animation:none}}
.threat .loc{color:var(--hi);font-weight:600;white-space:nowrap}
.threat .sct{color:var(--dim);font-variant-numeric:tabular-nums;letter-spacing:.04em;white-space:nowrap;flex:none}
.threat.hot .loc{color:#ffe3e6}
.threat .pct{display:flex;align-items:baseline;gap:5px;white-space:nowrap;flex:none}.threat .pct i{font-style:normal;font-size:9px;letter-spacing:.14em;color:var(--mute)}.threat .pct b{color:var(--hi);font-weight:600;font-variant-numeric:tabular-nums}
#app .threat #scrBtn{margin-left:auto!important;margin-right:0!important}#app .threat #bellBtn{margin-left:6px!important}
/* ===== on foot / EVA dash (tablet + desktop) ===== */
.footdash{display:none}
#app.onfoot:not(.phone) #page-ship.on{grid-template-columns:minmax(0,1.1fr) minmax(0,1.2fr) 300px!important;grid-template-rows:minmax(0,1fr) auto auto!important;gap:12px}
#app.onfoot:not(.phone) #page-ship .footdash{display:flex}
#app.onfoot:not(.phone) #page-ship .mapp{grid-column:1!important;grid-row:1!important}
#app.onfoot:not(.phone) #page-ship .bodycard{grid-column:2;grid-row:1;min-height:0;flex-direction:column;gap:7px}
#app.onfoot:not(.phone) #page-ship .suitpanel{grid-column:3;grid-row:1/4;min-height:0;padding:6px 4px!important}
#app.onfoot:not(.phone) #page-ship .suitpanel svg{width:100%;height:100%;display:block}
#app.onfoot:not(.phone) #page-ship .msncard{grid-column:1!important;grid-row:2!important}
#app.onfoot:not(.phone) #page-ship .sidecol.foot{grid-column:2!important;grid-row:2!important;display:block!important;overflow:visible}
#app.onfoot:not(.phone) #page-ship .sidecol.foot>:not(.lastship){display:none!important}
#app.onfoot:not(.phone) #page-ship .sumrow>.panel.foot{display:none!important}
#app.onfoot:not(.phone) #page-ship .deck.foot{grid-column:1/3!important;grid-row:3!important}
#app.onfoot.portrait:not(.phone) #page-ship.on{grid-template-columns:minmax(0,1fr) 300px!important;grid-template-rows:minmax(0,1fr) minmax(0,1.3fr) auto auto auto!important}
#app.onfoot.portrait:not(.phone) #page-ship .mapp{grid-column:1!important;grid-row:1!important}
#app.onfoot.portrait:not(.phone) #page-ship .bodycard{grid-column:1;grid-row:2}
#app.onfoot.portrait:not(.phone) #page-ship .suitpanel{grid-column:2;grid-row:1/5}
#app.onfoot.portrait:not(.phone) #page-ship .msncard{grid-column:1!important;grid-row:3!important}
#app.onfoot.portrait:not(.phone) #page-ship .sidecol.foot{grid-column:1!important;grid-row:4!important}
#app.onfoot.portrait:not(.phone) #page-ship .deck.foot{grid-column:1/3!important;grid-row:5!important}
.bodycard .lamps{display:flex;flex-wrap:wrap;gap:5px}
.lmp.o{color:#ffc9a3;border-color:#ff8a3d;background:rgba(255,138,61,.18);box-shadow:0 0 9px rgba(255,138,61,.4)}
.bmid{flex:1;min-height:0;display:grid;grid-template-columns:minmax(0,.8fr) minmax(0,1.2fr);gap:10px}
.bfigw{min-height:0;display:flex;justify-content:center;align-items:center;background:radial-gradient(ellipse at 50% 55%,rgba(91,209,230,.08),transparent 65%)}
.bfigw svg.bfig{height:100%;width:auto;max-width:100%;max-height:340px}
.blist{display:flex;flex-direction:column;gap:4px;min-width:0;min-height:0;overflow:hidden}
.bsec{font-size:9px;letter-spacing:.18em;color:var(--dim);display:flex;justify-content:space-between;gap:6px;margin-top:2px}.bsec em{font-style:normal;color:var(--c);letter-spacing:.04em;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.bslot{display:flex;gap:7px;align-items:center;padding:3px 7px 3px 3px;background:linear-gradient(90deg,rgba(18,55,68,.7),rgba(8,25,35,.6));border:1px solid #2b6b80;min-width:0;flex:none}
.bslot .sic{width:24px;height:24px;flex:none;display:grid;place-items:center;border:1px solid var(--line);background:#071a22;color:var(--c)}.bslot .sic svg.ic{width:15px;height:15px}
.bslot .stx{min-width:0;flex:1;display:flex;flex-direction:column}.bslot .sl{font-size:9px;letter-spacing:.14em;color:var(--dim);display:flex;gap:5px;align-items:center}
.bslot .sn{font-size:11.5px;color:var(--hi);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.bslot.inj{border-color:var(--ic);background:linear-gradient(90deg,color-mix(in srgb,var(--ic) 18%,transparent),rgba(8,25,35,.6))}.bslot.inj .sic{color:var(--ic);border-color:var(--ic)}
.bslot .cls{margin-left:0;font-size:8.5px}.bslot .ichip{font-size:8.5px;padding:0 4px}
.bgrp{display:flex;flex-direction:column;gap:4px;min-width:0}
#app.onfoot.portrait:not(.phone) .bmid{grid-template-columns:minmax(0,.55fr) minmax(0,1.45fr)}
#app.onfoot.portrait:not(.phone) .blist{flex-direction:row;gap:8px}#app.onfoot.portrait:not(.phone) .bgrp{flex:1}
.bcnts{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:4px;margin-top:2px}
.bcnt{display:flex;flex-direction:column;align-items:center;padding:3px 2px;border:1px solid var(--line);background:rgba(0,0,0,.3)}
.bcnt i{font-style:normal;font-size:8.5px;letter-spacing:.12em;color:var(--dim)}.bcnt b{font:700 15px ui-monospace,Consolas,monospace;color:var(--hi)}.bcnt small{font-size:9px;color:var(--mute)}
.bcnt.w{border-color:var(--a)}.bcnt.w b{color:var(--a)}
.bfoot{display:flex;justify-content:space-between;align-items:flex-end;border-top:1px solid var(--line);padding-top:5px}
.bfoot i{display:block;font-style:normal;font-size:8.5px;letter-spacing:.16em;color:#9fd2de}.bfoot b{font:700 20px ui-monospace,Consolas,monospace}
.bfoot .r{text-align:right}.bfoot .r b{font-family:inherit;font-size:13px;font-weight:600;color:var(--hi)}
/* last ship: silhouette */
.lssil{display:none}
#app.onfoot:not(.phone) .lastship{opacity:1;border-style:solid}
#app.onfoot:not(.phone) .lastship .ls{grid-template-columns:104px minmax(0,1fr) repeat(4,auto)!important;column-gap:12px}
#app.onfoot.portrait:not(.phone) .lastship .ls{grid-template-columns:104px minmax(0,1fr) repeat(4,auto)!important}
#app.onfoot:not(.phone) .lssil{display:grid;place-items:center;height:68px;border:1px solid #15303a;border-radius:2px;background:radial-gradient(ellipse at center,#0e2830,#050d11);--kc:#5be69a}
.lssil .silo{width:96px;height:64px}
/* vitals boxes */
[data-rings="full"]{display:block!important}
.vgrid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:6px}
.vgrid.ph{grid-template-columns:repeat(2,minmax(0,1fr));gap:4px}
.vbox{min-width:0;padding:5px 6px 7px;background:#03121a;border:1px solid var(--line)}
.vbox .vl{display:block;font-size:8.5px;letter-spacing:.16em;color:var(--dim);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.vbox .vv{display:flex;justify-content:flex-end;align-items:baseline;gap:3px;margin:1px 0 9px;white-space:nowrap}
.vbox .vv b{font:700 18px ui-monospace,Consolas,monospace;line-height:1.1}.vbox .vv small{font-size:8.5px;color:var(--dim)}
.vbox .vs{position:relative;display:block;height:4px;background:#12262c}.vbox .vs em{position:absolute;top:0;bottom:0}
.vbox .vs u{position:absolute;top:-7px;width:0;height:0;margin-left:-4px;border-left:4px solid transparent;border-right:4px solid transparent;border-top:5px solid var(--hi)}
.vgrid.ph .vbox{padding:4px 5px 6px}.vgrid.ph .vbox .vv b{font-size:15px}.vgrid.ph .vbox .vv small{font-size:8.5px}

/* SCREEN key */
.bell.scr{min-width:34px}
.bell.scr .led{position:absolute;top:3px;left:3px;width:5px;height:5px;border-radius:50%;background:#2a3a40;box-shadow:inset 0 1px 1px rgba(0,0,0,.6)}
.bell.scr.on{color:var(--g)}.bell.scr.on .led{background:var(--g);box-shadow:0 0 6px var(--g)}
/* Home Screen app with a translucent status bar: keep clear of the notch and home bar */
@supports (margin:env(safe-area-inset-top)){#app{margin:env(safe-area-inset-top) env(safe-area-inset-right) env(safe-area-inset-bottom) env(safe-area-inset-left)}}
/* ===== session tiles ===== */
.stiles{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px}
#app:not(.phone) .stiles.sessgrid{grid-template-columns:repeat(4,minmax(0,1fr))!important}
#page-you>.panel:has(.sessgrid){container-type:inline-size}
@container (min-width:1240px){#app:not(.phone) .stiles.sessgrid{grid-template-columns:repeat(8,minmax(0,1fr))!important}}
.stiles.pplsess{grid-template-columns:repeat(3,minmax(0,1fr))}
.stile{display:grid;grid-template-columns:auto minmax(0,1fr);grid-template-rows:auto auto;column-gap:9px;align-items:center;padding:7px 9px;min-width:0;
 background:linear-gradient(#0c1e26,#071318);border:1px solid var(--line);border-radius:3px;box-shadow:inset 0 1px 0 rgba(255,255,255,.04),inset 0 -8px 14px rgba(0,0,0,.35)}
.stile .sti{grid-row:1/3;width:32px;height:32px;display:grid;place-items:center;border-radius:50%;border:1px solid var(--c2);color:var(--c);
 background:radial-gradient(circle at 50% 35%,#143a47,#08161c);box-shadow:0 0 8px rgba(91,209,230,.18),inset 0 1px 2px rgba(0,0,0,.6)}
.stile .sti .ic{width:17px;height:17px}
.stile .stl{font-size:9px;letter-spacing:.14em;color:var(--dim);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;align-self:end}
.stile b{font:700 18px ui-monospace,Consolas,monospace;color:var(--hi);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;align-self:start}
.stile.g b{color:var(--g)}.stile.g .sti{color:var(--g);border-color:#1d5a3b;box-shadow:0 0 8px rgba(91,230,154,.2),inset 0 1px 2px rgba(0,0,0,.6)}
.stile.dim b{color:var(--dim);font-size:14px}
.stiles.sessgrid{gap:6px}.sessgrid .stile{padding:4px 9px}.sessgrid .stile .sti{width:28px;height:28px}.sessgrid .stile .sti .ic{width:15px;height:15px}.sessgrid .stile b{font-size:17px}
#app.phone .stiles{grid-template-columns:repeat(2,minmax(0,1fr))!important}#app.phone .stiles.pplsess{grid-template-columns:1fr!important}
#app.phone .stile .sti{width:28px;height:28px}#app.phone .stile b{font-size:16px}
/* PPL ships destroyed */
.ktal{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:8px}
.ktal span{display:flex;align-items:baseline;gap:8px;padding:3px 10px;border:1px solid var(--line);border-top:2px solid var(--tc);background:rgba(0,0,0,.25);font-size:9px;letter-spacing:.14em;color:var(--dim)}
.ktal b{font:700 17px ui-monospace,Consolas,monospace;color:var(--hi)}
.ktal .you,.kby.you{--tc:var(--g)}.ktal .party,.kby.party{--tc:var(--c)}.ktal .other,.kby.other{--tc:var(--a)}.ktal .crash,.kby.crash{--tc:var(--mute)}
.kills{display:flex;flex-direction:column;gap:5px}
#app:not(.phone) #page-ppl .pplships{display:flex;flex-direction:column;overflow:hidden}
#app:not(.phone) #pplShips .kills{flex:1;min-height:0;overflow:auto;padding-bottom:18px;-webkit-mask-image:linear-gradient(#000 calc(100% - 28px),transparent);mask-image:linear-gradient(#000 calc(100% - 28px),transparent);scrollbar-width:thin}
#pplShips .krow{flex:none}
.krow{--kc:#ff5468;display:grid;grid-template-columns:72px minmax(0,1fr) auto minmax(0,.9fr) 64px;gap:12px;align-items:center;padding:4px 10px 4px 4px;
 background:linear-gradient(90deg,rgba(0,0,0,.38),rgba(0,0,0,.12));border:1px solid var(--line);border-left:3px solid var(--kc)}
.krow.dis{--kc:#ffb347}.krow.old{opacity:.7}
.krow .sil{height:46px;display:grid;place-items:center;border:1px solid #15303a;border-radius:2px;background:radial-gradient(ellipse at center,#0e2830,#050d11);color:var(--kc)}
.silo{width:64px;height:42px}.silo polygon{fill:color-mix(in srgb,var(--kc) 20%,#061016);stroke:var(--kc);stroke-width:1.1;stroke-linejoin:round}
.krow .kn{display:flex;flex-direction:column;min-width:0}.krow .kn b{font-size:13px;color:var(--hi);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.krow .kn small{font-size:10px;color:var(--dim);white-space:nowrap}
.kst{font-size:9px;font-weight:700;letter-spacing:.1em;padding:3px 7px;border:1px solid var(--kc);color:color-mix(in srgb,var(--kc) 60%,#fff);background:color-mix(in srgb,var(--kc) 14%,transparent);border-radius:2px}
.kby{display:flex;align-items:center;gap:6px;min-width:0;font-size:11px;color:var(--mute)}.kby .ic{flex:none;color:var(--tc)}
.kby b{color:var(--tc);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-weight:600}
.krow .kt{text-align:right;font-size:11px;color:var(--mute);white-space:nowrap}
#app.phone .ktal{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:4px}#app.phone .ktal span{flex-direction:column;align-items:center;gap:1px;padding:3px 2px;font-size:8.5px;letter-spacing:.08em}#app.phone .ktal b{font-size:16px}
#app.phone .krow{grid-template-columns:58px minmax(0,1fr) auto;row-gap:2px;padding-right:8px}
#app.phone .krow .sil{grid-row:1/3;height:42px}#app.phone .silo{width:54px;height:36px}
#app.phone .krow .kby{grid-column:2;grid-row:2}#app.phone .krow .kt{grid-column:3;grid-row:2}#app.phone .krow .kn small{display:none}
/* countermeasure counts */
.cmrow{display:grid;grid-template-columns:repeat(auto-fit,minmax(0,1fr));gap:8px;margin-bottom:6px}
/* PPL, NAV: the growing list takes the free height */
#app:not(.phone) #page-ppl .pplships{flex:1;min-height:0;overflow:auto}
#app:not(.portrait):not(.phone) #page-ppl.on{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.3fr);grid-template-rows:auto minmax(0,1fr) auto;gap:10px}
#app:not(.portrait):not(.phone) #page-ppl>.two{display:contents!important}
#app:not(.portrait):not(.phone) #page-ppl #pplParty{grid-column:1;grid-row:1}
#app:not(.portrait):not(.phone) #page-ppl>.two>.panel:nth-child(2){grid-column:1;grid-row:2;min-height:0;overflow:auto}
#app:not(.portrait):not(.phone) #page-ppl .pplships{grid-column:2;grid-row:1/3}
#app:not(.portrait):not(.phone) #page-ppl>.panel:last-child{grid-column:1/-1;grid-row:3}
#app:not(.portrait):not(.phone) .navside>.panel:last-child{flex:1}
/* MSN, FEED: columns end at the same line */
#app:not(.portrait):not(.phone) #page-msn>#msnHist,#app:not(.portrait):not(.phone) #page-msn>#msnPinned{align-self:stretch!important}
#app:not(.portrait):not(.phone) #page-msn>#msnHist{min-height:0;overflow:auto}
#app:not(.portrait):not(.phone) #page-feed>.panel:first-child{align-self:stretch!important}
/* SYS: system tiles fill their panel */
#app:not(.portrait):not(.phone) #page-sys>.syspanel{display:flex;flex-direction:column;min-height:0}
#app:not(.portrait):not(.phone) #page-sys .sysgrid{flex:1;grid-auto-rows:minmax(52px,1fr);min-height:0}
#app:not(.portrait):not(.phone) #page-sys .sys{justify-content:center;gap:5px;padding:8px 12px;position:relative}
#app:not(.portrait):not(.phone) #page-sys .sys::before{content:"";position:absolute;top:7px;left:12px;width:24px;height:3px;border-radius:2px;background:var(--g);box-shadow:0 0 6px rgba(91,230,154,.7)}
#app:not(.portrait):not(.phone) #page-sys .sys.st-min::before{background:var(--a);box-shadow:0 0 6px rgba(255,179,71,.8)}
#app:not(.portrait):not(.phone) #page-sys .sys.st-maj::before{background:var(--r);box-shadow:0 0 8px rgba(255,84,104,.9)}
#app:not(.portrait):not(.phone) #page-sys .sys.st-off::before,#app:not(.portrait):not(.phone) #page-sys .sys.st-nd::before{background:#2a3a40;box-shadow:none}
#app:not(.portrait):not(.phone) #page-sys .sys .top{font-size:12px;gap:8px}
#app:not(.portrait):not(.phone) #page-sys .sys .top .ic{width:18px;height:18px}
#app:not(.portrait):not(.phone) #page-sys .sys .part{padding-left:26px}
/* on foot / EVA: one grid, top and bottom rows share their column lines */
#app.onfoot:not(.phone) #page-ship.on{display:grid!important;grid-template-columns:minmax(0,1.25fr) minmax(0,1fr);grid-template-rows:minmax(0,1fr) auto auto;gap:12px}
#app.onfoot:not(.phone) #page-ship .hero,#app.onfoot:not(.phone) #page-ship .sumrow{display:contents!important}
#app.onfoot:not(.phone) #page-ship .ann{display:none!important}
#app.onfoot:not(.phone) #page-ship .mapp{grid-column:1;grid-row:1;min-height:0}
#app.onfoot:not(.phone) #page-ship .sidecol.foot{grid-column:2;grid-row:1;min-height:0}
#app.onfoot:not(.phone) #page-ship .msncard{grid-column:1;grid-row:2}
#app.onfoot:not(.phone) #page-ship .sumrow>.panel.foot{grid-column:2;grid-row:2;min-width:0}
#app.onfoot:not(.phone) #page-ship .deck.foot{grid-column:1/-1;grid-row:3}
#app.onfoot.portrait:not(.phone) #page-ship.on{grid-template-columns:minmax(0,1fr) minmax(0,1fr);grid-template-rows:minmax(240px,1fr) auto auto auto}
#app.onfoot.portrait:not(.phone) #page-ship .mapp{grid-column:1/-1}
#app.onfoot.portrait:not(.phone) #page-ship .sidecol.foot{grid-column:1/-1;grid-row:2;grid-template-columns:minmax(0,1fr) minmax(0,1fr)!important;gap:12px}
#app.onfoot.portrait:not(.phone) #page-ship .msncard{grid-row:3}
#app.onfoot.portrait:not(.phone) #page-ship .sumrow>.panel.foot{grid-row:3}
#app.onfoot.portrait:not(.phone) #page-ship .deck.foot{grid-row:4}
/* ===== combat view (tablet + desktop, seated) ===== */
#page-ship{position:relative}
.cv,.mapdrawer{display:none}
#app.cview:not(.phone) #page-ship.on{grid-template-areas:"def def fly" "msn you fly" "deck deck fly"!important}
#app.cview.portrait:not(.phone) #page-ship.on{grid-template-areas:"def fly" "def fly" "msn fly" "you fly" "deck deck"!important}
#app.cview:not(.phone) #page-ship .mapp{position:absolute!important;left:5px;top:5px;bottom:5px;width:min(62%,640px);z-index:8;transform:translateX(calc(-100% - 30px));transition:transform .3s ease;box-shadow:0 0 0 4px #1b2327,0 0 0 5px #3c474d,14px 0 34px rgba(0,0,0,.75)!important}
#app.cview.mappeek:not(.phone) #page-ship .mapp{transform:none}
#app.cview:not(.phone) #page-ship .mapdrawer{display:flex;position:absolute;left:-1px;top:38%;z-index:9;flex-direction:column;align-items:center;gap:6px;padding:12px 5px;writing-mode:vertical-rl;font:inherit;font-size:9px;letter-spacing:.2em;color:var(--c);cursor:pointer;
 border:1px solid var(--c2);border-left:0;border-radius:0 5px 5px 0;background:linear-gradient(90deg,#123744,#0a2129);box-shadow:3px 0 10px rgba(0,0,0,.6);transition:left .3s ease}
#app.cview:not(.phone) #page-ship .mapdrawer::after{content:"";position:absolute;inset:-8px -10px -8px -4px}
#app.cview.mappeek:not(.phone) #page-ship .mapdrawer{left:calc(min(62%,640px) + 9px)}
#app.cview:not(.phone) #page-ship .defense{display:grid!important;grid-template-columns:minmax(0,1fr) minmax(0,1.3fr) minmax(0,.85fr);grid-template-rows:auto auto minmax(0,1fr) auto;
 grid-template-areas:"ph ph ph" "ann ann ann" "wpn ship cm" "log log log";gap:8px 14px}
#app.cview.portrait:not(.phone) #page-ship .defense{grid-template-columns:minmax(0,1fr) minmax(0,.9fr);grid-template-rows:auto auto minmax(0,1fr) auto auto;grid-template-areas:"ph ph" "ann ann" "ship ship" "wpn cm" "log log"}
#app.cview:not(.phone) #page-ship .defense>.ph{grid-area:ph;margin-bottom:0}
#app.cview:not(.phone) #page-ship .defense>.defrow{grid-area:ship;min-height:0}
#app.cview:not(.phone) #page-ship .defense>.dlist{display:none!important}
#app.cview:not(.phone) #page-ship .cv{display:flex}
.cann{grid-area:ann;flex-wrap:wrap;gap:5px;align-items:center}.cann i{width:1px;height:18px;background:var(--line);margin:0 3px}
.lmp{font-size:9px;font-weight:700;letter-spacing:.08em;padding:4px 8px;border:1px solid;border-radius:2px}
.lmp.r{color:#ffb3bb;border-color:#ff5468;background:rgba(255,84,104,.22);box-shadow:0 0 10px rgba(255,84,104,.45)}
.lmp.a{color:#ffd79a;border-color:#ffb347;background:rgba(255,179,71,.16);box-shadow:0 0 8px rgba(255,179,71,.35)}
.lmp.g{color:#5be69a;border-color:#1d5a3b;background:rgba(91,230,154,.08)}.lmp.off{color:#4f6f78;border-color:#22343a}
.lmp.blink{animation:lblink .8s steps(2) infinite}@keyframes lblink{50%{opacity:.35}}
.cwpn,.ccm{flex-direction:column;gap:5px;min-width:0;min-height:0;overflow:hidden}.cwpn{grid-area:wpn}.ccm{grid-area:cm}
.cv .sub{font-size:9px;letter-spacing:.18em;color:var(--dim);display:flex;justify-content:space-between;gap:6px}.cv .sub em{font-style:normal;color:var(--c);letter-spacing:.06em;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.gun{display:grid;grid-template-columns:auto minmax(0,1fr) auto;gap:8px;align-items:center;padding:4px 8px;background:rgba(0,0,0,.3);border:1px solid var(--line);border-left:3px solid}
.gun b{font-size:10px;color:#9fd2de;border:1px solid var(--c2);padding:1px 4px}.gun span{font-size:12px;color:var(--hi);display:flex;flex-direction:column;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.gun small{font-size:9px;color:var(--dim)}
.gun i{font-style:normal;font-size:9px;font-weight:700;letter-spacing:.1em}.gun.rdy{border-left-color:var(--g)}.gun.rdy i{color:var(--g)}.gun.chg{border-left-color:var(--a)}.gun.chg i{color:var(--a)}.gun.off{border-left-color:var(--mute)}.gun.off i{color:var(--mute)}
.cv .chips{display:flex;flex-wrap:wrap;gap:4px;margin-top:auto}.cv .chips span{font-size:8.5px;letter-spacing:.1em;padding:3px 6px;border:1px solid #22343a;color:var(--dim)}.cv .chips span.on{color:var(--c);border-color:var(--c2)}
.cmx{display:flex;justify-content:space-between;align-items:baseline;padding:3px 10px;background:rgba(0,0,0,.3);border:1px solid var(--line)}.cmx span{font-size:10px;letter-spacing:.16em;color:var(--dim)}
.cmx b{font:700 24px ui-monospace,Consolas,monospace;color:var(--hi)}.cmx b.lo{color:var(--a)}
.msl{display:grid;grid-template-columns:repeat(4,1fr);gap:4px}.msl i{height:20px;border:1px solid var(--c2);background:linear-gradient(#0f2f3a,#081a20);clip-path:polygon(50% 0,100% 30%,100% 100%,0 100%,0 30%)}
.note2{font-size:9.5px;color:var(--mute)}
.clog{grid-area:log;gap:16px;align-items:center;font-size:11px;color:#9fb3ba;border-top:1px solid var(--line);padding-top:6px;white-space:nowrap;overflow:hidden}.clog b{color:var(--hi)}.clog .kc{margin-left:auto}
.mapback{font:inherit;font-size:10px;letter-spacing:.14em;color:var(--c);background:linear-gradient(#123744,#0a2129);border:1px solid var(--c2);padding:4px 10px;border-radius:3px;cursor:pointer;position:relative}
.mapback::after{content:"";position:absolute;inset:-8px -4px}
#app.cview.portrait .clog{flex-wrap:wrap;white-space:normal;row-gap:4px}#app.cview.portrait .clog>span{white-space:nowrap}
/* room for the MAP tab on the left edge */
#app.cview:not(.phone) #page-ship.on{padding-left:26px}
.cvcal{font-size:9px;letter-spacing:.12em;color:var(--g);margin-left:6px}
@media (prefers-reduced-motion:reduce){#app.cview #page-ship .mapp,#app.cview #page-ship .mapdrawer{transition:none}.lmp.blink{animation:none}}
/* ===== cockpit dash look: matte housing, glass screens in metal bezels, physical keys ===== */
#app{background:url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='420' height='300'><g stroke='%23ffffff' stroke-linecap='round' fill='none'><path d='M40 60 l38 -6' stroke-opacity='.05'/><path d='M300 40 l22 9' stroke-opacity='.04'/><path d='M120 240 l50 -3' stroke-opacity='.035'/><path d='M360 210 l-18 14' stroke-opacity='.05'/><path d='M200 120 l9 2' stroke-opacity='.06'/><path d='M60 180 q20 -4 34 2' stroke-opacity='.03'/></g></svg>"),url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='180' height='180'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='.85' numOctaves='3' stitchTiles='stitch'/><feColorMatrix values='0 0 0 0 1  0 0 0 0 1  0 0 0 0 1  0 0 0 .07 0'/></filter><rect width='100%25' height='100%25' filter='url(%23n)'/></svg>"),radial-gradient(ellipse at 30% 0%,#20292e 0,#141a1d 55%,#0d1113 100%)}
#app.tablet{border:1px solid #2d363b!important;box-shadow:0 0 0 9px #0a0d0e,0 0 0 10px #262e32,0 24px 70px #000,inset 0 1px 0 rgba(255,255,255,.06)!important;border-radius:22px!important}
#app.phone{border-radius:26px}
body{background:#07090a!important}
.panel,.threat,.phrow,.phglance,.phmap,.phdmg,#bellPanel{clip-path:none!important}
.panel,.phglance,.phrow,.phmap,.phdmg{position:relative;border:1px solid #000!important;border-radius:3px;background:radial-gradient(ellipse at 50% 0%,#082029 0,#04121a 70%)!important;
 box-shadow:0 0 0 4px #1b2327,0 0 0 5px #3c474d,0 3px 8px 3px rgba(0,0,0,.5),inset 0 0 22px rgba(0,0,0,.7),inset 0 0 0 1px rgba(91,209,230,.08)!important}
.panel::before{content:"";position:absolute;inset:1px;pointer-events:none;border-radius:4px;z-index:3;
 background:radial-gradient(circle,#9aa4a9 0 1.2px,#3a4449 1.8px,transparent 2.3px) 0 0/8px 8px no-repeat,radial-gradient(circle,#9aa4a9 0 1.2px,#3a4449 1.8px,transparent 2.3px) 100% 0/8px 8px no-repeat,radial-gradient(circle,#9aa4a9 0 1.2px,#3a4449 1.8px,transparent 2.3px) 0 100%/8px 8px no-repeat,radial-gradient(circle,#9aa4a9 0 1.2px,#3a4449 1.8px,transparent 2.3px) 100% 100%/8px 8px no-repeat}
.panel::after,.phglance::after,.phmap::after{content:"";position:absolute;inset:0;pointer-events:none;border-radius:3px;z-index:2;
 background:linear-gradient(128deg,rgba(255,255,255,.05) 0,rgba(255,255,255,0) 30%),repeating-linear-gradient(0deg,rgba(0,0,0,.1) 0 1px,transparent 1px 3px)}
#app.sdalarm .panel{box-shadow:0 0 0 4px #3a1218,0 0 0 5px #ff2d43,inset 0 0 22px rgba(0,0,0,.7)!important}
.threat{border-radius:3px;border:1px solid #000!important;background:#0e0b0c!important;box-shadow:0 0 0 4px #1b2327,0 0 0 5px #3c474d,inset 0 0 14px rgba(0,0,0,.8)}
.threat.hot{background:linear-gradient(90deg,#2a0b12,#120a0c)!important}
.threat .chip.r,.threat .chip.r.v{background:linear-gradient(rgba(255,84,104,.26),rgba(120,16,30,.32))!important;border:1px solid #ff5468!important;color:#ffb3bb!important;border-radius:2px;box-shadow:0 0 10px rgba(255,84,104,.45),inset 0 0 8px rgba(255,84,104,.25)!important;text-shadow:0 0 6px rgba(255,84,104,.6)}
.threat .chip.a,.threat .chip.a.v{background:linear-gradient(rgba(255,179,71,.2),rgba(120,80,10,.25))!important;border:1px solid #ffb347!important;color:#ffd79a!important;border-radius:2px;box-shadow:0 0 8px rgba(255,179,71,.35),inset 0 0 8px rgba(255,179,71,.18)!important;text-shadow:0 0 6px rgba(255,179,71,.5)}
.threat .chip.c{border-radius:2px}
/* keys: side rail and deck */
.osb,.btn{clip-path:none!important;border-radius:5px!important;border:1px solid #0a0d0e!important;text-shadow:0 1px 0 #000;
 background:radial-gradient(ellipse at 50% 70%,rgba(255,255,255,.04),transparent 60%),linear-gradient(#2c363b,#181f23 60%,#12181b)!important;
 box-shadow:0 3px 0 #050708,0 4px 6px rgba(0,0,0,.6),inset 0 1px 0 rgba(255,255,255,.1),inset 0 -2px 0 rgba(0,0,0,.35)!important;color:#9fb3ba!important}
.osb:active,.btn:active{transform:translateY(2px);box-shadow:0 1px 0 #050708,inset 0 1px 0 rgba(255,255,255,.06)!important}
.osb::after,.btn:not(.split):not(.opm)::after{content:"";position:absolute;top:5px;left:50%;width:18px;height:3px;transform:translateX(-50%);border-radius:2px;background:#1d2a2f;pointer-events:none}
.osb.on{color:#c8f4ff!important;font-weight:700;text-shadow:0 0 8px rgba(91,209,230,.7)}.osb.on::after{background:var(--c);box-shadow:0 0 8px var(--c)}
.btn.lit{color:#c9ffe2!important}.btn.lit::after{background:var(--g)!important;box-shadow:0 0 8px var(--g)}
.btn.lit small{color:#8fd8b2!important}
.btn small{color:#6f8790!important}
.btn.guard{color:#ffe3b0!important;background:repeating-linear-gradient(135deg,rgba(214,160,20,.5) 0 7px,rgba(16,16,16,.92) 7px 14px),linear-gradient(#2c363b,#12181b)!important;box-shadow:0 3px 0 #050708,0 0 0 2px #4a3a10,inset 0 1px 0 rgba(255,255,255,.1)!important}
.btn.guard .bl,.btn.guard small{position:relative;z-index:1;padding:1px 5px;white-space:normal;text-align:center;max-width:100%;border-radius:2px;background:rgba(8,10,11,.92);color:#ffd89a!important}
.btn.guard svg.ic{position:relative;z-index:1;background:rgba(8,10,11,.85);border-radius:3px;padding:2px}
.btn.armed{background:linear-gradient(#6b1f2a,#3a0e14)!important;color:#fff!important;box-shadow:0 0 14px var(--r)!important}
.btn.sdbtn{background:linear-gradient(#a3122a,#5a0812)!important;color:#fff!important}
.btn .seg{border-radius:3px}
.bell{border-radius:3px;background:linear-gradient(#2a3439,#161c20);box-shadow:0 2px 0 #050708,inset 0 1px 0 rgba(255,255,255,.08)}

.deck.foot.emotes:empty{display:none}

.needbox[hidden],#needFull[hidden]{display:none!important}
#app:not(.portrait):not(.phone) #page-feed.on{grid-template-columns:minmax(0,1fr)}
#app:not(.portrait):not(.phone) #page-feed>.panel{grid-column:1!important;grid-row:1!important}
#app:not(.portrait):not(.phone) #page-ppl.on{grid-template-columns:minmax(0,1fr);grid-template-rows:minmax(0,1fr) auto}
#app:not(.portrait):not(.phone) #page-ppl .pplships{grid-column:1;grid-row:1}
#app:not(.portrait):not(.phone) #page-ppl>.panel:last-child{grid-column:1;grid-row:2}

.waitp{position:fixed;left:50%;top:50%;transform:translate(-50%,-50%);z-index:50;display:flex;flex-direction:column;gap:6px;align-items:center;padding:18px 26px;background:rgba(4,14,20,.94);border:1px solid var(--c2);border-radius:4px;color:var(--hi);font-size:13px;text-align:center}.waitp b{font-size:15px;letter-spacing:.12em;color:var(--c)}.waitp span{color:var(--dim);font-size:12px}.waitp[hidden]{display:none}
"""

SLATE_BODY = r"""
<svg width="0" height="0" style="position:absolute">
 <defs>
  <symbol id="i-thr" viewBox="0 0 24 24"><path d="M7 4h10v6l-2 3H9l-2-3z"/><path d="M10 15l-1 5M14 15l1 5M12 15v6"/></symbol>
  <symbol id="i-wings" viewBox="0 0 24 24"><path d="M12 6v12M12 9L3 13l1 2 8-2M12 9l9 4-1 2-8-2"/><path d="M5 7l2 2M19 7l-2 2" stroke-dasharray="1.5 1.5"/></symbol>
  <symbol id="i-atc" viewBox="0 0 24 24"><path d="M9 21l1.5-9h3L15 21M8 21h8M10 12V8h4v4M12 8V5"/><path d="M7 4a7 7 0 0 1 10 0"/></symbol>
  <symbol id="i-hailc" viewBox="0 0 24 24"><path d="M4 5h16v10H10l-4 4v-4H4z"/><path d="M8 10h.01M12 10h.01M16 10h.01"/></symbol>
  <symbol id="i-eye" viewBox="0 0 24 24"><path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/></symbol>
  <symbol id="i-exit" viewBox="0 0 24 24"><path d="M14 4h5v16h-5M10 8l-4 4 4 4M6 12h9"/></symbol>
  <symbol id="i-cross" viewBox="0 0 24 24"><circle cx="12" cy="12" r="7"/><path d="M12 2v6M12 16v6M2 12h6M16 12h6"/></symbol>
  <symbol id="i-missile" viewBox="0 0 24 24"><path d="M5 19l3-3M14 4l6 0 0 6-9 9-3-3-3-3z"/><path d="M8 13l-3 1 2 2M11 16l-1 3-2-2"/></symbol>
  <symbol id="i-scan" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 12l6-5"/><circle cx="12" cy="12" r="4.5" stroke-dasharray="2 2"/></symbol>
  <symbol id="i-mine" viewBox="0 0 24 24"><path d="M4 20l9-9M13 11l5-6 2 2-6 5M15 9l-3-3"/><path d="M5 9l2-3 3 1M17 17l2 3"/></symbol>
  <symbol id="i-salv" viewBox="0 0 24 24"><path d="M4 18l4-4 2 2 4-6 2 2 4-6"/><path d="M4 21h16"/><circle cx="8" cy="7" r="2"/></symbol>
  <symbol id="i-gear" viewBox="0 0 24 24"><path d="M12 3v8M9 8l3 3 3-3M8 14h8M12 14v3"/><circle cx="12" cy="19.5" r="2.5"/></symbol>
  <symbol id="i-doors" viewBox="0 0 24 24"><path d="M4 21V4h11v17M15 4l5 3v14H4"/><circle cx="12" cy="13" r=".9"/></symbol>
  <symbol id="i-bell" viewBox="0 0 24 24"><path d="M6 16v-5a6 6 0 0 1 12 0v5l2 2H4z"/><path d="M10 20a2 2 0 0 0 4 0"/></symbol><symbol id="i-lock" viewBox="0 0 24 24"><rect x="5" y="11" width="14" height="10" rx="1.5"/><path d="M8 11V8a4 4 0 0 1 8 0v3M12 15v2"/></symbol>
  <symbol id="i-lights" viewBox="0 0 24 24"><path d="M4 9h5a4 4 0 0 1 0 8H4zM13 8l7-2M13 13h8M13 18l7 2"/></symbol>
  <symbol id="i-qt" viewBox="0 0 24 24"><circle cx="15" cy="12" r="5"/><circle cx="15" cy="12" r="1.6"/><path d="M2 9h6M3 12h5M2 15h6"/></symbol>
  <symbol id="i-scm" viewBox="0 0 24 24"><circle cx="12" cy="12" r="7"/><path d="M12 2v5M12 17v5M2 12h5M17 12h5"/></symbol>
  <symbol id="i-nav" viewBox="0 0 24 24"><path d="M4 17l8-10 8 10M8 19l4-5 4 5"/></symbol>
  <symbol id="i-land" viewBox="0 0 24 24"><path d="M3 20h18M7 20v-3h10v3M12 3v8M9 8l3 3 3-3"/></symbol>
  <symbol id="i-ping" viewBox="0 0 24 24"><circle cx="12" cy="17" r="1.6"/><path d="M8 13a6 6 0 0 1 8 0M5 10a10 10 0 0 1 14 0M2 7a14 14 0 0 1 20 0"/></symbol>
  <symbol id="i-hail" viewBox="0 0 24 24"><path d="M12 21v-9M9 21h6M12 12l-3-6h6z"/><path d="M17 6a4 4 0 0 1 0 6M19.5 4a7 7 0 0 1 0 10"/></symbol>
  <symbol id="i-ready" viewBox="0 0 24 24"><path d="M12 3v7"/><path d="M7 6a7 7 0 1 0 10 0"/><path d="M9 14l2 2 4-4"/></symbol>
  <symbol id="i-eject" viewBox="0 0 24 24"><path d="M9 21v-6h6v6M8 15l1-7h6l1 7M12 2l-4 4h8z"/></symbol>
  <symbol id="i-destr" viewBox="0 0 24 24"><path d="M12 2l1.8 5.5L19 5l-2.5 5L22 12l-5.5 2L19 19l-5.2-2.5L12 22l-1.8-5.5L5 19l2.5-5L2 12l5.5-2L5 5l5.2 2.5z"/></symbol>
  <symbol id="i-vtol" viewBox="0 0 24 24"><path d="M5 18h14M8 18v-4h8v4M12 11V3M9 6l3-3 3 3"/></symbol>
  <symbol id="i-coupled" viewBox="0 0 24 24"><path d="M10 14l4-4M9 17l-2 2a3 3 0 0 1-4-4l3-3a3 3 0 0 1 4 0M15 7l2-2a3 3 0 0 1 4 4l-3 3a3 3 0 0 1-4 0"/></symbol>
  <symbol id="i-power" viewBox="0 0 24 24"><path d="M12 2v9M6.3 5.7a8 8 0 1 0 11.4 0"/></symbol>
  <symbol id="i-shield" viewBox="0 0 24 24"><path d="M12 2l8 3v6c0 5-3.5 9-8 11-4.5-2-8-6-8-11V5z"/></symbol>
  <symbol id="i-party" viewBox="0 0 24 24"><circle cx="8" cy="8" r="3"/><circle cx="16" cy="9" r="2.5"/><path d="M2 20a6 6 0 0 1 12 0M13 20a5 5 0 0 1 9-3"/></symbol>
  <symbol id="i-contract" viewBox="0 0 24 24"><path d="M6 2h9l4 4v16H6z"/><path d="M9 10h7M9 14h5M14 18l3-2-3-2"/></symbol>
  <symbol id="i-friend" viewBox="0 0 24 24"><circle cx="10" cy="8" r="3.5"/><path d="M3 21a7 7 0 0 1 14 0M19 8v6M16 11h6"/></symbol>
  <symbol id="i-obj" viewBox="0 0 24 24"><path d="M12 3l9 9-9 9-9-9z"/><circle cx="12" cy="12" r="2"/></symbol>
  <symbol id="i-zone" viewBox="0 0 24 24"><path d="M12 2l9 4v6c0 5-4 8.5-9 10-5-1.5-9-5-9-10V6z"/><path d="M8 12h8"/></symbol>
  <symbol id="i-pay" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M15 9h-4a2 2 0 0 0 0 4h2a2 2 0 0 1 0 4H9M12 6v2M12 17v1"/></symbol>
  <symbol id="i-bank" viewBox="0 0 24 24"><path d="M3 10l9-6 9 6M5 10v8M9.5 10v8M14.5 10v8M19 10v8M3 20h18"/></symbol><symbol id="i-route" viewBox="0 0 24 24"><circle cx="6" cy="18" r="2"/><circle cx="18" cy="6" r="2"/><path d="M8 18h6a3 3 0 0 0 0-6h-4a3 3 0 0 1 0-6h6"/></symbol><symbol id="i-bp" viewBox="0 0 24 24"><path d="M6 3h9l4 4v14H6z"/><path d="M15 3v4h4M9 12h7M9 16h7M9 12v4"/></symbol><symbol id="i-downed" viewBox="0 0 24 24"><circle cx="6" cy="14" r="2"/><path d="M9 15h11M9 15l3-3h5M3 19h18"/></symbol><symbol id="i-respawn" viewBox="0 0 24 24"><path d="M20 12a8 8 0 1 1-2.3-5.7"/><path d="M20 4v4h-4"/><circle cx="12" cy="12" r="2"/></symbol><symbol id="i-seat" viewBox="0 0 24 24"><path d="M7 4v9a2 2 0 0 0 2 2h7l2 5M7 15l-1 5M9 10h6"/></symbol><symbol id="i-kill" viewBox="0 0 24 24"><circle cx="12" cy="12" r="7"/><path d="M12 2v5M12 17v5M2 12h5M17 12h5"/><circle cx="12" cy="12" r="1.5"/></symbol><symbol id="i-screen" viewBox="0 0 24 24"><path d="M4 9V5h4M20 9V5h-4M4 15v4h4M20 15v4h-4"/><rect x="8.5" y="9.5" width="7" height="5" rx="1"/></symbol><symbol id="i-skull" viewBox="0 0 24 24"><path d="M5 11a7 7 0 0 1 14 0v4l-2 1v3H7v-3l-2-1z"/><circle cx="9" cy="12" r="1.5"/><circle cx="15" cy="12" r="1.5"/><path d="M11 19v-2M13 19v-2"/></symbol>
  <symbol id="t-ship" viewBox="0 0 24 24"><path d="M12 2l3 9 6 4-6 1-1 5h-4l-1-5-6-1 6-4z"/></symbol>
  <symbol id="t-msn" viewBox="0 0 24 24"><path d="M12 3l9 9-9 9-9-9z"/><circle cx="12" cy="12" r="2"/></symbol>
  <symbol id="t-feed" viewBox="0 0 24 24"><path d="M4 5h16M4 10h16M4 15h10M4 20h7"/></symbol>
  <symbol id="t-nav" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M15 9l-2 6-4 0 2-6z"/></symbol>
  <symbol id="t-ppl" viewBox="0 0 24 24"><circle cx="8" cy="8" r="3"/><circle cx="16" cy="9" r="2.5"/><path d="M2 20a6 6 0 0 1 12 0M13 20a5 5 0 0 1 9-3"/></symbol>
  <symbol id="s-helmet" viewBox="0 0 24 24"><path d="M5 14a7 7 0 0 1 14 0v4H5z"/><path d="M8 14h8v3H8z"/></symbol>
  <symbol id="s-core" viewBox="0 0 24 24"><path d="M7 4h10l2 5-2 11H7L5 9z"/><path d="M9 9h6M10 13h4"/></symbol>
  <symbol id="s-arms" viewBox="0 0 24 24"><path d="M5 4l-2 10 3 1 2-8M19 4l2 10-3 1-2-8"/><path d="M8 4h8"/></symbol>
  <symbol id="s-legs" viewBox="0 0 24 24"><path d="M7 3h10l-1 18h-3l-1-11-1 11H8z"/></symbol>
  <symbol id="s-suit" viewBox="0 0 24 24"><path d="M9 3h6l1 4 3 2-2 3-2-1v10H9V11l-2 1-2-3 3-2z"/></symbol>
  <symbol id="s-pack" viewBox="0 0 24 24"><rect x="6" y="6" width="12" height="15" rx="2"/><path d="M9 6V4h6v2M9 12h6"/></symbol>
  <symbol id="s-tool" viewBox="0 0 24 24"><path d="M4 14l6-6 3 3-6 6zM13 5l6 6M15 3l6 6-2 2-6-6z"/></symbol>
  <symbol id="s-mag" viewBox="0 0 24 24"><path d="M9 3h6v6l-1 12h-4L9 9z"/><path d="M9 9h6"/></symbol>
  <symbol id="s-gren" viewBox="0 0 24 24"><circle cx="12" cy="14" r="6"/><path d="M10 8V5h4v3M14 5l4-2"/></symbol>
  <symbol id="i-weapon" viewBox="0 0 24 24"><path d="M3 12h11l3-3h4v6h-4l-3-3M6 12v4h3v-4"/></symbol>
  <symbol id="v-hp" viewBox="0 0 24 24"><path d="M12 20s-7-4.5-7-10a4 4 0 0 1 7-2.6A4 4 0 0 1 19 10c0 5.5-7 10-7 10z"/><path d="M9 11h6M12 8v6"/></symbol>
  <symbol id="v-o2" viewBox="0 0 24 24"><circle cx="9" cy="13" r="5"/><circle cx="17" cy="7" r="3"/><circle cx="18" cy="15" r="1.6"/><path d="M7.5 13a1.5 1.5 0 0 1 3 0 1.5 1.5 0 0 1-3 0"/></symbol>
  <symbol id="v-food" viewBox="0 0 24 24"><path d="M7 3v8M5 3v5a2 2 0 0 0 4 0V3M7 11v10M16 3c-2 1-3 4-3 7h3v11"/></symbol>
  <symbol id="v-water" viewBox="0 0 24 24"><path d="M12 3c3 4 6 7.5 6 11a6 6 0 0 1-12 0c0-3.5 3-7 6-11z"/><path d="M9 15a3 3 0 0 0 3 3"/></symbol>
  <symbol id="v-temp" viewBox="0 0 24 24"><path d="M10 14V5a2 2 0 0 1 4 0v9a4 4 0 1 1-4 0z"/><path d="M12 9v7"/></symbol>
  <symbol id="v-pulse" viewBox="0 0 24 24"><path d="M2 12h4l2-5 4 10 3-7 2 2h5"/></symbol>
  <symbol id="v-rad" viewBox="0 0 24 24"><circle cx="12" cy="12" r="2"/><path d="M12 10L8.5 4a9 9 0 0 1 7 0zM10.3 13L3.4 13a9 9 0 0 0 3.5 6zM13.7 13l6.9 0a9 9 0 0 1-3.5 6z"/></symbol>
  <symbol id="v-air" viewBox="0 0 24 24"><path d="M3 9h11a3 3 0 1 0-3-3M3 14h15a3 3 0 1 1-3 3M3 19h7"/></symbol>
  <symbol id="t-you" viewBox="0 0 24 24"><path d="M12 21s-8-5-8-11a4.5 4.5 0 0 1 8-3 4.5 4.5 0 0 1 8 3c0 6-8 11-8 11z"/></symbol>
 </defs>
</svg>

<div id="app">
 <div class="brand"><svg viewBox="0 0 90 110"><defs><linearGradient id="brC" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#3f8fa0"/><stop offset="1" stop-color="#2a6c7a"/></linearGradient></defs><polygon points="45,4 86,58 68,58 45,30 22,58 4,58" fill="url(#brC)"/><polygon points="45,44 70,78 52,78 45,68 38,78 20,78" fill="#8a979e"/><line x1="45" y1="90" x2="45" y2="104" stroke="#3f8fa0" stroke-width="6" stroke-linecap="round"/></svg><b>KESTREL</b><i>K-7 FLIGHT SLATE</i></div>
 <div class="threat cut" id="threat"></div>

 <nav class="bezel" id="bezel">
  <button class="osb cut on" data-p="ship"><svg class="ic"><use href="#t-ship"/></svg>MAIN</button>
  <button class="osb cut" data-p="sys"><svg class="ic"><use href="#i-power"/></svg>SYS</button>
  <button class="osb cut" data-p="msn"><svg class="ic"><use href="#t-msn"/></svg>MSN</button>
  <button class="osb cut" data-p="feed"><svg class="ic"><use href="#t-feed"/></svg>FEED<span class="bdg" id="bdg"></span></button>
  <button class="osb cut" data-p="nav"><svg class="ic"><use href="#t-nav"/></svg>NAV</button>
  <button class="osb cut" data-p="ppl"><svg class="ic"><use href="#t-ppl"/></svg>PPL</button>
  <button class="osb cut" data-p="you"><svg class="ic"><use href="#t-you"/></svg>YOU</button>
  <button class="osb cut phonly" id="moreBtn"><svg class="ic"><use href="#t-feed"/></svg>MORE</button>
 </nav>

 <main>
  <!-- SHIP -->
  <section class="page on" id="page-ship">
   <button class="mapdrawer" id="mapDrawer" type="button" title="map"><span>MAP</span><b>▸</b></button>
   <div class="phmain" id="phmain"></div>
   <div class="ann seat" id="ann"></div>
   <div class="hero">
    <div class="panel cut mapp">
     <div class="ph"><span class="sysbtns"><button class="sysbtn on" data-sysn="stanton">STANTON</button><button class="sysbtn" data-sysn="pyro">PYRO</button><button class="sysbtn" data-sysn="nyx">NYX<i>5.0</i></button></span><span class="mapmodes"><button class="on" data-z="sys">SYSTEM</button><button data-z="loc">LOCAL</button></span><span class="r" id="mapPlace"></span></div>
     <div class="dmgp" id="dmgp"></div>
     <div class="mapwrap"><button class="dhandle" type="button" title="missions and planets">MISSIONS <span>◂</span></button>
      <svg id="mapSys" viewBox="0 0 600 420" preserveAspectRatio="xMidYMid meet"></svg>
      <svg id="mapLoc" viewBox="0 0 600 420" preserveAspectRatio="xMidYMid meet" style="display:none"></svg>
      <div class="dist"></div>
     </div>
    </div>
    <div class="sidecol seat">
     <div class="panel cut defense">
      <div class="ph"><span id="defName">DEFENSE</span><span class="r"><span id="layoutTag"></span> · 5.0</span></div>
      <div class="shipid unit" id="shipId"></div>
      <div class="defrow"><svg class="shipbig" id="defsvg" viewBox="0 0 280 236" preserveAspectRatio="xMidYMid meet"></svg></div>
      <div class="dlist" id="dlist"></div>
      <div class="cv cann" id="cAnn"></div><div class="cv cwpn" id="cWpn"></div><div class="cv ccm" id="cCm"></div><div class="cv clog" id="cLog"></div>
      <div class="shipstate" id="shipState"></div>
      <div class="shiplegend"><span><i style="background:#ffb347"></i>WARNING</span><span><i style="background:#ff5468"></i>CRITICAL</span><span class="lgs"><i class="lg rdy"></i>READY</span><span class="lgs"><i class="lg chg"></i>NOT READY</span><span class="lgs"><i class="lg off"></i>OFF</span><span class="lgb"><i class="lg tme"></i>YOUR TURRET</span><span class="lgb"><i class="lg tcr"></i>CREWED</span></div>
     </div>
     <div class="panel cut gunneronly">
      <div class="ph">TURRET · GUNNER<span class="r" id="gunWhere"></span></div>
      <div class="wl" id="gunlist"></div>
      <div class="fbars" style="margin-top:6px">
       <div><div class="kv">DECOYS <b id="gDec">—</b></div><div class="bar"><i id="gDecb" style="width:0"></i></div></div>
       <div><div class="kv">NOISE <b id="gNoi">—</b></div><div class="bar a"><i id="gNoib" style="width:0"></i></div></div>
      </div>
     </div>
     <div class="panel cut flight pilotonly"><svg id="hudsvg" viewBox="0 0 300 772" preserveAspectRatio="xMidYMid meet"></svg>
      <div class="ph">FLIGHT<span class="r"><span id="mode">—</span><span class="unit" id="spool"></span></span></div>
      <div class="spdo"><svg id="spdo" viewBox="0 0 220 136" preserveAspectRatio="xMidYMid meet"></svg>
       <div class="spdc"><span class="big" id="spd">—</span><span class="unit">m/s</span><div class="vsi na" id="vsi">VSI —</div></div></div>
      <div class="spdl" id="spdl"></div>
      <div class="fstate" id="fstate"></div>
      <div class="hdg" id="hdg"></div>
      <div class="fgrid">
       <div class="kv">ALT <b id="alt">—</b></div><div class="kv">G <b><span id="g">—</span><span class="unit" id="gmax"></span></b></div>
       <div class="kv">GROUND <b id="agl">—</b></div><div class="kv">AIR <b id="atm">—</b></div>
      </div>
      <div class="fbars">
       <div><div class="kv">H₂ <b id="h2t">—</b></div><div class="bar"><i id="h2" style="width:0"></i></div></div>
       <div><div class="kv">QT <b id="qtt">—</b></div><div class="bar"><i id="qtb" style="width:0"></i></div></div>
       <div><div class="kv">BOOST <b id="bst">—</b></div><div class="bar a"><i id="bstb" style="width:0"></i></div></div>
      </div>
      <div class="ph sub">IFCS<span class="r unit">lit = active · outline = on</span></div>
      <div class="pchips" id="ifcs"></div>
      <div class="ph sub">POWER</div>
      <div class="pchips" id="pips"></div>
     </div>
    </div>
    <div class="sidecol foot">
     <div class="panel cut lastship">
      <div class="ph">LAST SHIP<span class="r"></span></div>
      <div class="ls">
       <span class="lssil" id="lsSil"></span>
       <div class="lsname"><div class="title" id="lsTitle">—</div><div class="unit lsl2"><span id="lsFull"></span><span id="lsSep"> · </span><span id="lsWhere"></span></div></div>
       <div class="lsstats">
        <div class="lsv"><span class="unit">HULL</span><b id="lsHull">—</b></div>
        <div class="lsv"><span class="unit">SHIELDS</span><b id="lsSh">—</b></div>
        <div class="lsv"><span class="unit">H₂</span><b id="lsH2">—</b></div>
        <div class="lsv"><span class="unit">QT</span><b id="lsQt">—</b></div>
       </div>
      </div>
     </div>
     <div class="panel cut evaonly o2hero">
      <div class="o2ring" id="o2ring"></div>
      <div>
       <div class="unit">OXYGEN · EVA</div>
       <div class="o2big" id="o2v">—</div>
       <div class="o2left">about <b id="o2t">—</b> left at this rate</div>
       <div class="unit" style="margin-top:4px" id="o2env"></div>
      </div>
     </div>
     <div class="panel cut loadcard">
      <div class="ph">LOADOUT<span class="r" data-go="you" style="cursor:pointer">full list on YOU ▸</span></div>
      <div data-load="mini"></div>
     </div>
     <div class="panel cut" style="flex:1">
      <div class="ph">YOU<span class="r" id="youWhere"></span></div>
      <div style="display:grid;grid-template-columns:1fr auto;gap:10px;align-items:center">
       <div class="rings big" data-rings="full"></div>
       <div class="figwrap big" data-fig></div>
      </div>
     </div>
    </div>
   </div>
   <div class="panel cut footdash bodycard" id="bodyCard"></div>
   <div class="panel cut footdash suitpanel"><svg id="suitsvg" viewBox="0 0 300 800" preserveAspectRatio="xMidYMid meet"></svg></div>
   <div class="sumrow">
    <div class="panel cut msncard" data-go="msn" style="cursor:pointer"></div>
    <div class="panel cut foot" data-go="ppl" style="cursor:pointer;grid-template-columns:1fr">
     <div class="ph">NEARBY</div>
     <div id="mainNear"><div class="line unit">No ships destroyed nearby</div></div>
    </div>
    <div class="panel cut seat envcard" data-go="you" style="cursor:pointer">
     <div class="ph">YOU<span class="r hrx">—</span></div>
     <div class="envv" id="envVerdict"></div>
     <div class="envg" id="envGrid"></div>
     <div class="envvit" id="envVit"></div>
     <div class="envinj" id="envInj"></div>
    </div>
    <div class="panel cut seat oldyou" data-go="you" style="cursor:pointer">
     <div class="ph">YOU<span class="r hrx">—</span></div>
     <div class="youcard">
      <div class="rings small" data-rings="mini"></div>
      <div class="figwrap" data-fig></div>
     </div>
    </div>
   </div>
   <div class="deck seat" id="deck"></div>
   <div class="deck foot emotes"></div>
  </section>

  <!-- MISSIONS -->
  <section class="page" id="page-sys">
   <div class="two" style="display:grid;grid-template-columns:1fr 1fr;gap:10px">
    <div class="panel cut">
     <div class="ph">POWER<span class="r" id="pgNote">tap to switch</span></div>
     <div class="deck" id="pgroups"></div>
     <div class="unit" style="margin-top:10px">On or off per group. No pip levels in the game data.</div>
    </div>
    <div class="panel cut" id="cmPanel"></div>
   </div>
   <div class="panel cut list sysweap">
    <div class="ph">WEAPONS<span class="r">live from telemetry</span></div>
    <div id="wlist"></div>
   </div>
   <div class="panel cut syspanel">
    <div class="ph" id="sysHead">SYSTEMS</div>
    <div class="sysgrid" id="sysgrid"></div>
    <div class="unit" id="sysNote" style="margin-top:8px"></div>
   </div>
   <div class="panel cut pilotonly setpanel">
    <div class="ph">SETTINGS<span class="r">from the game · change them in game</span></div>
    <div class="setgrid" id="setgrid"></div>
   </div>
  </section>

  <section class="page" id="page-msn">
   <div class="panel cut" id="msnPinned"></div>
   <div class="panel cut list" id="msnList"></div>
   <div class="panel cut list" id="msnHist"></div>
  </section>

  <!-- FEED -->
  <section class="page" id="page-feed">
   <div id="needFull" hidden></div>
   <div class="panel cut scrolls" style="flex:1;min-height:0;overflow:auto">
    <div class="filters" id="filters">
     <button class="on" data-f="all">ALL</button><button data-f="msn">MISSIONS</button><button data-f="soc">SOCIAL</button><button data-f="zone">ZONES</button><button data-f="cbt">COMBAT</button><button data-f="pay">MONEY</button>
    </div>
    <div id="feedFull"></div>
   </div>
  </section>

  <!-- NAV -->
  <section class="page" id="page-nav">
   <div class="navgrid">
    <div class="panel cut mapp navmapp">
     <div class="ph"><span class="sysbtns"><button class="sysbtn on" data-sysn="stanton">STANTON</button><button class="sysbtn" data-sysn="pyro">PYRO</button><button class="sysbtn" data-sysn="nyx">NYX<i>5.0</i></button></span><span class="mapmodes"><button class="on" data-z="sys">SYSTEM</button><button data-z="loc">LOCAL</button></span><span class="r">tap a planet for LOCAL</span></div>
     <div class="mapwrap"><svg id="navSys" viewBox="80 0 440 420" preserveAspectRatio="xMidYMid meet"></svg><svg id="navLoc" viewBox="80 0 440 420" preserveAspectRatio="xMidYMid meet" style="display:none"></svg><div class="dist" id="navDist"></div></div>
    </div>
    <div class="navside">
     <div class="panel cut">
      <div class="ph">WHERE YOU ARE</div>
      <div style="font-size:18px;color:var(--hi)" id="navPlace">—</div>
      <div class="kv" style="margin-top:8px">ZONE <b id="navZone">—</b></div>
      <div class="kv">JURISDICTION <b id="navJur">—</b></div>
     </div>
          <div class="panel cut list">
      <div class="ph">RECENT PLACES</div>
      <div id="navStops"><div class="row unit">No places yet</div></div>
     </div>
    </div>
   </div>
  </section>

  <!-- PEOPLE -->
  <section class="page" id="page-ppl">
   <div class="panel cut list pplships scrolls" id="pplShips"></div>
   <div class="panel cut list">
    <div class="ph">THIS SESSION<span class="r">from Vehicle Destroyed and Actor Death</span></div>
    <div class="stiles pplsess"><div class="stile"><span class="sti"><svg class="ic"><use href="#i-kill"/></svg></span><span class="stl">SHIPS DESTROYED</span><b id="pDst">—</b></div><div class="stile"><span class="sti"><svg class="ic"><use href="#i-skull"/></svg></span><span class="stl">DEATHS</span><b id="pDead">—</b></div><div class="stile"><span class="sti"><svg class="ic"><use href="#i-downed"/></svg></span><span class="stl">DOWNED</span><b id="pDown">—</b></div></div>
   </div>
  </section>

  <!-- YOU -->
  <section class="page" id="page-you">
   <div class="youtop">
    <div class="panel cut doll">
     <div class="ph">LOADOUT · BODY<span class="r">gear from Game.log · injuries from HUD notices</span></div>
     <div class="dollgrid">
      <div class="slots left" id="slotsL"></div>
      <div class="dollfig" id="dollFig"></div>
      <div class="slots right" id="slotsR"></div>
     </div>
     <div class="carry" id="carry"></div>
    </div>
    <div class="youside">
     <div class="panel cut">
      <div class="ph">VITALS</div>
      <div class="rings big" data-rings="full"></div>
      <span id="hr" style="display:none"></span><span id="hrb" style="display:none"></span>
     </div>
     <div class="panel cut" id="suitPanel"></div>
    </div>
   </div>
   <div class="panel cut">
    <div class="ph">THIS SESSION<span class="r" id="sessT"></span></div>
    <div class="stiles sessgrid">
     <div class="stile g"><span class="sti"><svg class="ic"><use href="#i-pay"/></svg></span><span class="stl">WALLET</span><b id="sWal">—</b></div><div class="stile "><span class="sti"><svg class="ic"><use href="#i-bank"/></svg></span><span class="stl">BALANCE</span><b id="sBal">—</b></div><div class="stile "><span class="sti"><svg class="ic"><use href="#i-route"/></svg></span><span class="stl">FLOWN</span><b id="sFlown">—</b></div><div class="stile "><span class="sti"><svg class="ic"><use href="#i-bp"/></svg></span><span class="stl">BLUEPRINTS</span><b id="sBp">—</b></div><div class="stile "><span class="sti"><svg class="ic"><use href="#i-downed"/></svg></span><span class="stl">DOWNED</span><b id="sDown">—</b></div><div class="stile "><span class="sti"><svg class="ic"><use href="#i-skull"/></svg></span><span class="stl">DEATHS</span><b id="sDead">—</b></div><div class="stile "><span class="sti"><svg class="ic"><use href="#i-respawn"/></svg></span><span class="stl">RESPAWNS</span><b id="sResp">—</b></div><div class="stile "><span class="sti"><svg class="ic"><use href="#i-seat"/></svg></span><span class="stl">SEAT CHANGES</span><b id="sSeat">—</b></div>
    </div>
   </div>
  </section>
 </main>

 <aside class="rail" id="rail">
  <div class="panel cut needbox" hidden><div class="ph">ANSWER IN GAME<span class="r" id="needcount"></span></div><div id="needRail"></div></div>
  <div class="panel cut feedbox" style="flex:1;min-height:0;overflow:hidden"><div class="ph">FEED</div><div id="feedRail"></div></div>
 </aside>
</div>

<div id="waitPanel" class="waitp"><b>Waiting for the game</b><span>Start Star Citizen with telemetry on. This page updates by itself.</span></div>
"""

SLATE_JS = r"""
const $=s=>document.querySelector(s), $$=s=>[...document.querySelectorAll(s)];
const app=$('#app');
const ic=(id,cls='ic')=>`<svg class="${cls}"><use href="#${id}"/></svg>`;

/* ---- state ---- */
let combat=false, phone=false, foot=false, tablet=false, rot='land', hrNow=0, seat='pilot', lastSeat='pilot';
let lowVit=false;
const VIT=()=>{const v=MODEL.vitals,e=MODEL.env,r=x=>x==null?NaN:Math.round(x);return {intox:v.intox||0,hp:r(v.hp),o2:r(v.o2),food:r(v.food),water:r(v.water),temp:v.temp==null?NaN:+v.temp.toFixed(1),hr:r(v.hr),rad:e.rad||0,air:e.breathable!==false}};
function setSeat(s){
 if(s===seat)return;
 const words={pilot:'Sat in pilot seat',gunner:'Sat in gunner seat',foot:'Left the seat',eva:'Started EVA'};
 const icon={pilot:'t-ship',gunner:'i-hail',foot:'t-you',eva:'v-o2'};
 lastSeat=seat; seat=s; foot=(s==='foot'||s==='eva'); if(foot)combat=false;
 app.classList.add('morph'); setTimeout(()=>app.classList.remove('morph'),450);
 render();
}

function gunList(){
 const g=weapons;
 const grp={};g.forEach(w=>{const k=w[0]+'|'+w[1];(grp[k]=grp[k]||{n:w[0],s:w[1],c:0,r:0}).c++;if(w[3])grp[k].r++});
 $('#gunlist').innerHTML=Object.values(grp).map(x=>`<div class="row" style="display:flex;gap:8px;align-items:center;padding:5px 0;border-bottom:1px solid rgba(31,76,94,.4)"><span class="dot ${x.r===x.c?'':'a'}"></span>${x.c>1?x.c+' × ':''}${x.n} <span class="tag">${x.s}</span><span class="unit" style="margin-left:auto">${x.r===x.c?(x.c>1?'all ready':'ready'):x.r+' ready · '+(x.c-x.r)+' charging'}</span></div>`).join('');

}
/* ---- on foot / EVA: body card and suit cluster (actor telemetry + Game.log loadout) ---- */
function footEnv(){const e=MODEL.env,v=MODEL.vitals;return {breath:e.breathable!==false,temp:e.temp==null?0:Math.round(e.temp),press:v.pressure==null?0:+v.pressure.toFixed(2),grav:v.gravity==null?0:v.gravity}}
function bodyCard(){const el=$('#bodyCard');if(!el||!foot)return;
 const s=INJ(),L=LIVESET(),sh={h:'HEAD',t:'TORSO',la:'L ARM',ra:'R ARM',ll:'L LEG',rl:'R LEG'},word={min:'MINOR',mod:'MODERATE',sev:'SEVERE'},lc={min:'a',mod:'o',sev:'r'},cc={min:'#ffb347',mod:'#ff8a3d',sev:'#ff5468'};
 const hurt=Object.keys(sh).filter(k=>s[k]&&s[k]!=='ok'),rk={sev:0,mod:1,min:2};hurt.sort((a,b)=>rk[s[a]]-rk[s[b]]);
 const lamps=hurt.map(k=>`<span class="lmp ${lc[s[k]]}">${sh[k]} · ${word[s[k]]}</span>`).join('')+`<span class="lmp g">${hurt.length?'REST OK':'NO INJURIES'}</span>`;
 const worst=(...ks)=>ks.map(k=>s[k]).filter(v=>v&&v!=='ok').sort((a,b)=>rk[a]-rk[b])[0];
 const row=(icon,lab,name,extra='',val='',inj)=>`<div class="bslot${inj?' inj':''}"${inj?` style="--ic:${cc[inj]}"`:''}><span class="sic">${ic(icon)}</span><span class="stx"><span class="sl">${lab}${extra}</span><span class="sn">${esc(name)}</span></span>${val}</div>`;
 const armor=[['helmet','s-helmet','HELMET',['h']],['core','s-core','CORE',['t']],['arms','s-arms','ARMS',['la','ra']],['legs','s-legs','LEGS',['ll','rl']]].map(([k,icn,lab,ps])=>{const x=G(L[k]),w=worst(...ps);
  return row(icn,lab,x?x[0]:'none worn',(x&&x[2]?`<span class="cls cls${x[2]}">${x[2]}</span>`:'')+(w?`<span class="ichip" style="border-color:${cc[w]};color:${cc[w]}">${word[w]}</span>`:''),'',w)}).join('');
 const gear=Object.entries(L.w).slice(0,3).map(([slot,w])=>row('i-weapon',slot.toUpperCase(),w[0])).join('')||'<div class="unit">No weapons in the log yet</div>';
 const S=gearSlots(),cnt=(lab,v,max,warn)=>`<span class="bcnt${warn?' w':''}"><i>${lab}</i><b>${v}</b>${max!=null?`<small>/ ${max}</small>`:''}</span>`;
 const fig=bodySvg(s,'bfig',{fill:v=>v==='ok'?'#0f2a33':cc[v]+'88',stroke:v=>v==='ok'?'#5be69a':cc[v],sw:2,glow:true});
 const v=VIT(),R=suitRating();
 el.innerHTML=`<div class="ph">BODY<span class="r">${seat==='eva'?'EVA · outside the '+esc(SH().short):'on foot'}</span></div>
  <div class="lamps">${lamps}</div>
  <div class="bmid"><div class="bfigw">${fig}</div><div class="blist">
   <div class="bgrp"><div class="bsec">ARMOR<em>${esc(L.label)}</em></div>${armor}</div>
   <div class="bgrp"><div class="bsec">GEAR<em>from Game.log</em></div>${gear}</div>
  </div></div>
  <div class="bfoot"><span><i>HEALTH</i><b style="color:${v.hp<40?'#ff5468':v.hp<60?'#ffb347':'#5be69a'}">${isNaN(v.hp)?'—':v.hp+'%'}</b></span><span class="r"><i>SUIT RATED</i><b>${R.tmin==null?'—':R.tmin+' to '+R.tmax+' °C'}</b></span></div>`}
function suitHud(){const el=document.getElementById('suitsvg');if(!el||!el.getClientRects().length)return;
 const bb=el.getBoundingClientRect(),HV=Math.max(800,Math.round(300*bb.height/Math.max(1,bb.width))),GP=(HV-800)/4;el.setAttribute('viewBox',`0 0 300 ${HV}`);
 const C={bg:'#03121a',c:'#5bd1e6',c2:'#2b6b80',g:'#5be69a',a:'#ffb347',r:'#ff5468',dim:'#7aa3ae',line:'#1f4c5e',hi:'#eafcff',mute:'#4f6f78'};
 const NS=(t,a,txt)=>{let q=`<${t}`;for(const k in a)q+=` ${k}="${a[k]}"`;return q+(txt!==undefined?`>${txt}</${t}>`:'/>')};
 const T=(x,y,t,o={})=>NS('text',{x,y,'font-size':o.s||10,fill:o.f||C.dim,'text-anchor':o.a||'middle','font-weight':o.w||400,'letter-spacing':o.ls||0,'font-family':o.m?'ui-monospace,Consolas,monospace':'inherit'},t);
 const P=(cx,cy,r,a)=>[cx+r*Math.cos(a*Math.PI/180),cy+r*Math.sin(a*Math.PI/180)];
 const ARC=(cx,cy,r,a0,a1)=>{const[x0,y0]=P(cx,cy,r,a0),[x1,y1]=P(cx,cy,r,a1);return `M${x0.toFixed(1)} ${y0.toFixed(1)} A${r} ${r} 0 ${a1-a0>180?1:0} 1 ${x1.toFixed(1)} ${y1.toFixed(1)}`};
 const glow=c=>`style="filter:drop-shadow(0 0 4px ${c})"`,fr=(v,a,b)=>Math.max(0,Math.min(1,(v-a)/(b-a)));
 const box=(x,y,w,l,val,unit,col,sc)=>{let q=NS('rect',{x,y,width:w,height:64,fill:C.bg,stroke:C.line})+T(x+6,y+13,l,{a:'start',s:8.5,ls:'.16em'})+T(x+w-6,y+36,val,{a:'end',f:col,s:19,w:700,m:1})+T(x+w-6,y+48,unit,{a:'end',s:8.5});
  const sx=x+6,sw=w-12,sy=y+55;q+=NS('rect',{x:sx,y:sy,width:sw,height:4,fill:'#12262c'});for(const[z0,z1,c] of sc.z)q+=NS('rect',{x:sx+sw*z0,y:sy,width:sw*(z1-z0),height:4,fill:c});return sc.v==null?q:q+`<path d="M${(sx+sw*sc.v).toFixed(1)} ${sy-2} l-4 -5 h8z" fill="${C.hi}"/>`};
 const tank=(x,y,w,h,lab,f,col,txt)=>{let q=NS('rect',{x,y,width:w,height:h,rx:w/2,fill:'url(#sTank)',stroke:C.line,'stroke-width':1.5});
  q+=`<rect x="${x+4}" y="${(y+4+(h-8)*(1-f)).toFixed(1)}" width="${w-8}" height="${((h-8)*f).toFixed(1)}" rx="${(w-8)/2}" fill="${col}" opacity=".85" ${glow(col)}/>`;
  for(let i=1;i<4;i++)q+=NS('line',{x1:x+w,y1:y+h*i/4,x2:x+w+5,y2:y+h*i/4,stroke:C.mute});return q+T(x+w/2,y+h+14,lab,{s:8.5,ls:'.16em'})+T(x+w/2,y+h+28,txt,{s:12,f:C.hi,w:700,m:1})};
 const lamp=(x,y,w,t,st)=>{const S={g:['#5be69a','#0e2c1e'],a:['#ffb347','#3a2a0c'],r:['#ff5468','#3a0c14'],o:['#4f6f78','#0a161b']}[st];return NS('rect',{x,y,width:w,height:19,rx:2,fill:S[1],stroke:S[0],'stroke-opacity':st==='o'?.4:.9})+T(x+w/2,y+13,t,{s:8.5,f:S[0],w:700,ls:'.06em'})};
 const v=VIT(),E=footEnv(),R=suitRating(),eva=seat==='eva';
 let o=`<defs><linearGradient id="sTank" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#0a2a33"/><stop offset=".5" stop-color="#103a46"/><stop offset="1" stop-color="#0a2a33"/></linearGradient><radialGradient id="sFace"><stop offset="0" stop-color="#0b2430"/><stop offset="1" stop-color="#061219"/></radialGradient></defs>`;
 o+=T(6,14,'SUIT',{a:'start',s:9,ls:'.16em'})+T(294,14,LIVESET().label,{a:'end',s:9});
 // O2 dial, built like the speed dial
 const V=v.o2,cx=150,cy=146,R0=104,A0=135,A1=405,ang=p=>A0+(A1-A0)*p/100;
 o+=NS('circle',{cx,cy,r:R0+14,fill:'url(#sFace)',stroke:C.line});
 o+=NS('path',{d:ARC(cx,cy,R0,A0,ang(10)),stroke:'#4a1018','stroke-width':9,fill:'none'})+NS('path',{d:ARC(cx,cy,R0,ang(10),ang(25)),stroke:'#3a2e10','stroke-width':9,fill:'none'})+NS('path',{d:ARC(cx,cy,R0,ang(25),A1),stroke:'#12404d','stroke-width':9,fill:'none'});
 const vc=V<=10?C.r:V<=25?C.a:eva?C.a:C.c;if(V>0)o+=`<path d="${ARC(cx,cy,R0,A0,Math.max(A0+.5,ang(V)-.01))}" stroke="${vc}" stroke-width="9" fill="none" ${glow(vc)}/>`;
 for(let p=0;p<=100;p+=5){const a=ang(p),mj=p%25===0,[x0,y0]=P(cx,cy,R0-8,a),[x1,y1]=P(cx,cy,R0-(mj?20:14),a);o+=NS('line',{x1:x0,y1:y0,x2:x1,y2:y1,stroke:mj?C.hi:C.c2,'stroke-width':mj?1.6:1});if(mj){const[lx,ly]=P(cx,cy,R0-32,a);o+=T(lx,ly+3.5,p,{s:9,m:1})}}
 for(const[p,l,c] of [[10,'CRIT',C.r],[25,'LOW',C.a]]){const a=ang(p),[x0,y0]=P(cx,cy,R0+6,a),[x1,y1]=P(cx,cy,R0-6,a),[lx,ly]=P(cx,cy,R0+20,a);o+=NS('line',{x1:x0,y1:y0,x2:x1,y2:y1,stroke:c,'stroke-width':2.5})+T(lx,ly+3,l,{f:c,s:8.5,w:700})}
 {const[nx,ny]=P(cx,cy,R0-4,ang(V)),[bx,by]=P(cx,cy,-14,ang(V));o+=`<line x1="${bx.toFixed(1)}" y1="${by.toFixed(1)}" x2="${nx.toFixed(1)}" y2="${ny.toFixed(1)}" stroke="${C.hi}" stroke-width="3" stroke-linecap="round" ${glow(C.c)}/>`+NS('circle',{cx,cy,r:5,fill:'#0b2430',stroke:C.c,'stroke-width':2})}
 const mins=o2Mins();
 o+=T(cx,cy-36,'O₂',{s:10,ls:'.16em'})+T(cx,cy+60,V,{f:C.hi,s:34,w:700,m:1})+T(cx,cy+78,'% suit oxygen',{s:9,ls:'.16em'})+T(cx,cy+100,eva&&mins!=null?`≈ ${mins} min left at this rate`:'',{s:9,f:eva?(V<=25?C.r:C.a):C.dim});
 // outside
 const Y1=272+GP;o+=NS('line',{x1:6,y1:Y1,x2:294,y2:Y1,stroke:C.line})+T(6,Y1+16,'OUTSIDE',{a:'start',s:9,ls:'.16em'})+T(294,Y1+16,E.breath?'breathable':eva?'vacuum':'no air',{a:'end',s:9,f:E.breath?C.g:C.a});
 const tin=R.tmin==null||E.temp==null||E.temp>=R.tmin&&E.temp<=R.tmax,z=t=>fr(t,-250,150);
 o+=box(6,Y1+24,92,'TEMP',E.temp,'°C',tin?C.hi:C.r,{v:z(E.temp),z:[[0,z(R.tmin),'#4a1018'],[z(R.tmin),z(R.tmax),'#12404d'],[z(R.tmax),1,'#4a1018']]})
  +box(104,Y1+24,92,'PRESS',E.press.toFixed(2),'atm',E.press<.5?C.a:C.hi,{v:fr(E.press,0,2),z:[[0,.25,'#3a2e10'],[.25,1,'#12404d']]})
  +box(202,Y1+24,92,'GRAV',E.grav.toFixed(1),'g',C.hi,{v:fr(E.grav,0,2),z:[[0,1,'#12404d']]});
 // body
 const Y2=372+2*GP;o+=NS('line',{x1:6,y1:Y2,x2:294,y2:Y2,stroke:C.line})+T(6,Y2+16,'BODY',{a:'start',s:9,ls:'.16em'});
 const tc=p=>p<20?C.r:p<45?C.a:C.g;
 o+=tank(24,Y2+26,42,104,'FOOD',v.food/100,tc(v.food),v.food+'%')+tank(92,Y2+26,42,104,'WATER',v.water/100,tc(v.water),v.water+'%')+tank(160,Y2+26,42,104,'TEMP',fr(v.temp,34,40),C.c,v.temp+'°');
 o+=NS('rect',{x:222,y:Y2+26,width:72,height:132,fill:C.bg,stroke:C.line})+T(228,Y2+39,'HEART',{a:'start',s:8.5,ls:'.16em'});
 const hy=Y2+80;o+=`<polyline points="226,${hy} 238,${hy} 242,${hy-8} 247,${hy+12} 252,${hy-24} 257,${hy+20} 261,${hy} 290,${hy}" fill="none" stroke="${C.g}" stroke-width="1.6" ${glow(C.g)}/>`;
 o+=T(258,Y2+130,v.hr,{s:19,f:C.hi,w:700,m:1})+T(258,Y2+146,'bpm',{s:8.5});
 // status
 const Y3=548+3*GP;o+=NS('line',{x1:6,y1:Y3,x2:294,y2:Y3,stroke:C.line})+T(6,Y3+16,'STATUS',{a:'start',s:9,ls:'.16em'});
 const helm=!E.breath||E.press<.5||!tin;
 const L=[[E.breath?'AIR OK':'AIR NONE',E.breath?'g':'r'],[helm?'HELMET ON':'HELMET OFF',helm?(eva?'g':'a'):'o'],[tin?'TEMP OK':E.temp<R.tmin?'TOO COLD':'TOO HOT',tin?'g':'r'],
  [v.rad>1?'RAD HIGH':'RAD OK',v.rad>1?'a':'g'],[v.intox?'INTOX '+v.intox:'SOBER',v.intox?'a':'o'],[eva?'EVA':'ON FOOT',eva?'a':'g']];
 L.forEach((l,i)=>{o+=lamp(6+(i%3)*98,Y3+24+Math.floor(i/3)*25,90,l[0],l[1])});
 o+=box(6,Y3+80,140,'RADIATION',v.rad.toFixed(1),'REM/s',v.rad>1?C.a:C.hi,{v:fr(v.rad,0,10),z:[[0,.5,'#12404d'],[.5,.8,'#3a2e10'],[.8,1,'#4a1018']]})
  +box(152,Y3+80,142,'INTOX',v.intox,'level',v.intox?C.a:C.hi,{v:fr(v.intox,0,5),z:[[0,.4,'#12404d'],[.4,1,'#3a2e10']]});
 // wallet: uec_balance from the actor state
 const Y4=724+4*GP;o+=NS('line',{x1:6,y1:Y4,x2:294,y2:Y4,stroke:C.line})+T(6,Y4+16,'WALLET',{a:'start',s:9,ls:'.16em'})+T(294,Y4+16,'live',{a:'end',s:9,f:C.g});
 o+=T(294,Y4+54,(MODEL.wallet!=null?Math.round(MODEL.wallet).toLocaleString('en-US'):'—'),{a:'end',s:26,f:C.hi,w:700,m:1})+T(6,Y4+54,'aUEC',{a:'start',s:10});
 el.innerHTML=o}
function lastShipSil(){const e=$('#lsSil');if(e)e.innerHTML=silo(shipK,96,64)}
function o2(){
 if(seat!=='eva')return; const p=63, C=2*Math.PI*44, off=C*(1-p/100);
 $('#o2ring').innerHTML=`<svg viewBox="0 0 100 100" width="120" height="120"><circle cx="50" cy="50" r="44" fill="none" stroke="#0f2a33" stroke-width="8"/><circle cx="50" cy="50" r="44" fill="none" stroke="#ffb347" stroke-width="8" stroke-linecap="round" stroke-dasharray="${C}" stroke-dashoffset="${off}" transform="rotate(-90 50 50)"/><g transform="translate(32 32)"><svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="#ffb347" stroke-width="1.6" stroke-linecap="round"><use href="#v-o2"/></svg></g></svg>`;
}
function ring(id,label,pct,val,col,extra=''){if(/NaN/.test(String(val)))val='—';
 const C=2*Math.PI*22, off=C*(1-(isFinite(pct)?Math.max(0,Math.min(1,pct)):0));
 return `<div class="ring"><svg class="g" viewBox="0 0 56 56"><circle class="trk" cx="28" cy="28" r="22"/><circle class="arc" cx="28" cy="28" r="22" stroke="${col}" stroke-dasharray="${C}" stroke-dashoffset="${off}"/><g transform="translate(16 16)"><svg class="ico ${extra}" width="24" height="24" viewBox="0 0 24 24" stroke="${col}"><use href="#${id}"/></svg></g></svg><div class="val">${val}</div><div class="lab">${label}</div></div>`;
}
const lvl=p=>p<30?'#ff5468':p<55?'#ffb347':'#5be69a';
/* vitals as readout + scale boxes, the FLIGHT cluster's box in HTML */
const ZR='#4a1018',ZA='#3a2e10',ZT='#12404d';
function vbox(lab,val,unit,col,frac,zones,cls=''){if(val==null||/NaN/.test(String(val))){val='—';frac=null}const z=zones.map(([a,b,c])=>`<em style="left:${a*100}%;width:${(b-a)*100}%;background:${c}"></em>`).join('');
 return `<div class="vbox ${cls}"><span class="vl">${lab}</span><span class="vv"><b style="color:${col}">${val}</b><small>${unit}</small></span><i class="vs">${z}${frac==null?'':`<u style="left:${Math.max(0,Math.min(1,frac))*100}%"></u>`}</i></div>`}
function vitBox(k,o={}){const v=VIT(),fr=(x,a,b)=>(x-a)/(b-a),eva=seat==='eva';
 const cOf=(p,lo,mid)=>p<lo?'#ff5468':p<mid?'#ffb347':'#eafcff';
 switch(k){
  case 'hp':return vbox('HEALTH',v.hp,'%',cOf(v.hp,40,60),v.hp/100,[[0,.4,ZR],[.4,.6,ZA],[.6,1,ZT]]);
  case 'o2':{const m=o2Mins();return vbox('O₂',v.o2,eva&&m!=null?`% · ${m} min`:'%',v.o2<=10?'#ff5468':v.o2<=25||eva?'#ffb347':'#eafcff',v.o2/100,[[0,.1,ZR],[.1,.25,ZA],[.25,1,ZT]],o.cls)}
  case 'food':return vbox('FOOD',v.food,'%',cOf(v.food,20,45),v.food/100,[[0,.2,ZR],[.2,.45,ZA],[.45,1,ZT]]);
  case 'water':return vbox('WATER',v.water,'%',cOf(v.water,20,45),v.water/100,[[0,.2,ZR],[.2,.45,ZA],[.45,1,ZT]]);
  case 'temp':return vbox('BODY',v.temp,'°C',v.temp<35.5||v.temp>38.5?'#ffb347':'#eafcff',fr(v.temp,34,40),[[0,.25,ZA],[.25,.75,ZT],[.75,1,ZR]]);
  case 'hr':return vbox('HEART',v.hr,'bpm',v.hr>140?'#ff5468':v.hr>110?'#ffb347':'#eafcff',fr(v.hr,40,180),[[0,.43,ZT],[.43,.71,ZA],[.71,1,ZR]]);
  case 'rad':return vbox('RADIATION',v.rad.toFixed(1),'REM/s',v.rad>5?'#ff5468':v.rad>1?'#ffb347':'#eafcff',v.rad/10,[[0,.1,ZT],[.1,.5,ZA],[.5,1,ZR]]);
  case 'air':return vbox('AIR',v.air?'OK':'NONE','',v.air?'#5be69a':'#ff5468',null,[[0,1,v.air?'#1d5a3b':ZR]]);
  case 'intox':return vbox('INTOX',v.intox,'level',v.intox?'#ffb347':'#eafcff',v.intox/5,[[0,.4,ZT],[.4,1,ZA]]);
 }}
function rings(){
 const v=VIT();
 const mini=ring('v-hp','HEALTH',v.hp/100,v.hp+'%',lvl(v.hp))+ring('v-o2','OXYGEN',v.o2/100,v.o2+'%','#5bd1e6')+ring('v-food','FOOD',v.food/100,v.food+'%',lvl(v.food))+ring('v-water','WATER',v.water/100,v.water+'%',lvl(v.water));
 const full=mini+ring('v-temp','BODY',(v.temp-30)/12,v.temp+' °C','#5bd1e6')+ring('v-pulse','HEART',(v.hr-40)/140,v.hr+' bpm',v.hr>110?'#ffb347':'#5bd1e6','pulse')+ring('v-rad','RAD RATE',v.rad/10,v.rad.toFixed(1),'#5be69a')+ring('v-air','AIR',v.air?1:0,v.air?'OK':'NONE',v.air?'#5be69a':'#ff5468');
 $$('[data-rings="mini"]').forEach(e=>e.innerHTML=mini);
 const boxes=`<div class="vgrid">${['hp','o2','food','water','temp','hr','rad','air','intox'].map(k=>vitBox(k)).join('')}</div>`;
 $$('[data-rings="full"]').forEach(e=>e.innerHTML=boxes);
}
const SMALLGUNS=[];
let weapons=[], bigShip=false, shipK='';
const KW='Klaus & Werner ';
/* turret layout from scunpacked-data ships/*.json (stock loadout); gun names from CSR's sc_item_names.json */
/* every spaceship with a pilot seat: scunpacked-data ships.json + ships/<class>.json, stock loadout; gun names via CSR's sc_item_names.json */
let ALLSHIPS=[];
const pretty=hp=>{let w=hp.replace(/^hardpoint_/,'').replace(/(^|_)turret(_|$)/g,'$1$2').replace(/_+/g,' ').trim();w=w.replace(/\b0?(\d+)\b/,'$1');return (w[0]||'').toUpperCase()+w.slice(1)||'Turret'};
const niceShort=n=>n.replace(/^Constellation Mk (IV|V) /,'').replace(/ Edition$/,'').replace(/^F7C-M Super Hornet /,'F7C-M ').replace(/^Starlifter /,'');
/* hand-made layouts for popular ships override name-based spots */
/* game models (Data.p4k via unp4k + cgf-converter): [length m, width m, top-down outline (x right, y to the nose), hardpoint x/y] */
const SHAPE={};
/* S: Length, Width, Height (m), pilot gun ports in loadout order, Size */
const SDIM={};
/* S: FlightCharacteristics.Speeds [SCM, boost forward, NAV max] in m/s */
const SPEEDS={};
const LAYOUT={AEGS_Hammerhead_GS:{turret_top:[120,100],turret_side_front_left:[99,82],turret_side_front_right:[141,82],turret_side_back_left:[92,124],turret_side_back_right:[148,124],turret_rear:[120,146]}};
function fromCaps(c){const T=c.turrets.filter(t=>t[1]!=='p');
 return {full:c.name,short:niceShort(c.short),missiles:c.missiles||0,pdc:c.turrets.filter(t=>t[1]==='p').length,caps:c,
  pilot:c.pilotGunList.map(g=>[g[0],g[1]?'S'+g[1]:'',true,true,true]),
  slaved:c.slaved||[],
  turrets:T.map(t=>({id:t[0],n:pretty(t[0]),kind:t[1],mounts:t[2],gn:t[3]||'empty mount',sz:t[4]?'S'+t[4]:'',slave:!!t[5],guns:(t[6]||[]).map(g=>[g[0],g[1]?'S'+g[1]:'']),ctl:t[7]||(t[1]==='r'&&t[5]?'both':'gunner')}))}}
const SHIPS={};const NOSHIP=fromCaps({name:'',short:'',class:'',turrets:[],pilotGunList:[],seats:[],sys:{power:[],cool:[],shield:[],qd:[],jump:[],radar:[],life:[],mineLaser:[],salvHead:[],tractorBeam:[]}});
const hasGunnerSeat=k=>{const S=SHIPS[k];if(!S)return false;return S.turrets.some(t=>t.kind==='m'||t.kind==='r')&&(S.caps.seats||[]).some(x=>/turret|gunner/i.test(x[1]||''))};
/* name-based placement on the generic ship drawing (viewBox 240x210) */
function placeAll(k){const S=SHIPS[k], L=LAYOUT[k]||{}, used=[], out={};
 const X=n=>/left/.test(n)?(/side/.test(n)?-28:-21):/right/.test(n)?(/side/.test(n)?28:21):0;
 const Y=n=>/nose/.test(n)?62:/front|fwd/.test(n)?80:/tail/.test(n)?150:/rear|aft/.test(n)?140:/back/.test(n)?128:/centre|center|mid/.test(n)?104:104;
 S.turrets.forEach(t=>{const n=t.id.toLowerCase(), under=/lower|bottom|base_lower/.test(n);
  let xy=L[t.id]?[...L[t.id]]:[120+X(n),Y(n)+(under?8:0)];
  const free=([x,y])=>!used.some(([ux,uy])=>Math.hypot(ux-x,uy-y)<10)&&x>22&&x<218&&y>22&&y<188;
  if(!free(xy)){const R=[[11,0],[-11,0],[0,11],[0,-11],[11,11],[-11,11],[11,-11],[-11,-11],[22,0],[-22,0],[0,22],[0,-22],[22,11],[-22,11],[22,-11],[-22,-11],[11,22],[-11,22],[11,-22],[-11,-22],[33,0],[-33,0]];
   const hit=R.map(([dx,dy])=>[xy[0]+dx,xy[1]+dy]).find(free); if(hit)xy=hit}
  used.push(xy); out[t.id]={xy,under,hand:!!L[t.id]}});
 return out}
const SH=()=>SHIPS[shipK]||NOSHIP;
const gunLabel=t=>(t.gn.replace(KW,'')+' '+t.sz).trim();
const tLabel=t=>{if(!t.guns||!t.guns.length)return `${t.mounts} × ${gunLabel(t)}`;const g={};t.guns.forEach(([n,z])=>{const k=(n.replace(KW,'')+' '+z).trim();g[k]=(g[k]||0)+1});return Object.entries(g).map(([k,n])=>`${n} × ${k}`).join(' + ')};
const TG={}; const tg=t=>TG[shipK+t.id]||(TG[shipK+t.id]=t.guns&&t.guns.length?t.guns.map(g=>[g[0],g[1],true,true,true]):Array.from({length:t.mounts},()=>[t.gn,t.sz,true,true,true]));
const turretPick={};
const pilotTur=t=>t.ctl==='pilot'||t.ctl==='both';
const firstManned=()=>SH().turrets.find(t=>t.kind==='m')||SH().turrets.find(t=>t.kind==='r'&&t.ctl!=='pilot');
const myTurret=()=>bigShip&&seat==='gunner'&&firstManned()?(turretPick[shipK]||firstManned().id):null;
/* telemetry doesn't say which turret: every turret with the same guns is a candidate */
const seatExact=()=>{const t=SH().turrets.find(x=>x.id===myTurret());return !!t&&(SH().caps.seats||[]).some(x=>x[0]===t.id)};
const candidates=()=>{const me=SH().turrets.find(t=>t.id===myTurret());if(!me)return [];if(seatExact())return [me];return SH().turrets.filter(t=>(t.kind==='m'||t.kind==='r')&&t.ctl!=='pilot'&&tLabel(t)===tLabel(me))};
const myTurretObj=()=>SH().turrets.find(x=>x.id===myTurret());
function applyShip(){const S=SH();if(seat==='gunner'&&!hasGunnerSeat(shipK))seat='pilot';bigShip=S.turrets.some(t=>t.kind==='m'||t.kind==='r');const t=myTurretObj();weapons=t?tg(t):S.pilot.concat(S.turrets.filter(pilotTur).flatMap(x=>tg(x)));if(MODEL.weapons&&MODEL.weapons.length)weapons=MODEL.weapons.map(w=>w.slice())}
let FIREGROUP='', CM={decoy:null,noise:null};
/* T: WeaponsFrame.preset_name + WeaponsSettings */
let WSET={gimbals:false,stagger:false};
function gunsInner(){
 if(bigShip){const S=SH(), tot=S.turrets.reduce((a,t)=>a+t.mounts,0)+S.pilot.length, t=myTurretObj();
  const rd=weapons.filter(w=>w[2]&&w[3]).length, dots=`<span class="gdots">${weapons.map(w=>`<i class="${!w[2]?'off':w[3]?'rdy':'chg'}"></i>`).join('')}</span>`;
  const cand=candidates(), unsure=t&&cand.length>1&&!turretPick[shipK];
  const mine=t?`<div class="dgl"><span class="dgn">${unsure?'TURRET':t.n.toUpperCase()}</span><b>${rd}/${weapons.length}</b>${dots}<span class="unit dgx">${unsure?`one of ${cand.length} · tap it on the ship`:'your seat · from seat name'}</span></div>`
   :S.pilot.length?`<div class="dgl"><span class="dgn">PILOT GUNS</span><b>${rd}/${weapons.length}</b>${dots}<span class="unit dgx">${CM.decoy} decoys · ${CM.noise} noise</span></div>`
   :`<div class="dgl"><span class="dgn">YOUR SEAT</span><span class="dgu">pilot · no guns · ${CM.decoy} decoys · ${CM.noise} noise</span></div>`;
  return mine+`<div class="dgl dgship"><span class="unit">${S.short} · ${S.pilot.length?'pilot guns + ':''}${S.turrets.length} turrets · ${tot} guns${S.pdc?` · ${S.pdc} point defense`:''} · other seats not live</span></div>`}
 if(!weapons.length)return `<div class="dgl"><span class="dgn">GUNS</span><span class="dgu">none on this ship</span><span class="unit dgx">${SH().caps.mining?'mining laser not in telemetry':SH().caps.salvage?'salvage heads not in telemetry':''}</span></div>`;
 const n=weapons.length, rd=weapons.filter(w=>w[2]&&w[3]).length, up=weapons.filter(w=>!w[2]).length;
 return `<div class="dgl"><span class="dgn">GUNS</span><b>${rd}/${n}</b><span class="gdots">${weapons.map(w=>`<i class="${!w[2]?'off':w[3]?'rdy':'chg'} ${w[4]?'':'nog'}" title="${esc(w[0])} ${w[1]}"></i>`).join('')}</span>${up?`<span class="dgu">${up} off</span>`:''}<span class="unit dgx">${FIREGROUP} · gimbals ${WSET.gimbals?'locked':'free'}${WSET.stagger?' · staggered':''} · ${CM.decoy} decoys · ${CM.noise} noise</span></div>`}

const PG={pw:{l:'ALL POWER',i:'i-power'},en:{l:'ENGINES',i:'i-thr'},sh:{l:'SHIELDS',i:'i-shield'},wp:{l:'WEAPONS',i:'i-cross'}};
let PGON={pw:true,en:true,sh:true,wp:true};
const partTxt=g=>(g||[]).map(([n,sz,c])=>`${c>1?c+' × ':''}${n}${sz?' · S'+sz:''}`).join(', ');
function settings(){const el=$('#setgrid');if(!el)return;const r=(n,v,t)=>`<div class="sr">${n}<b class="${v?'y':''}">${t||(v?'on':'off')}</b></div>`;
 const I=MODEL.ifcs||{},S=MODEL.weaponSettings||{},a=k=>I[k]?(I[k].active?'active':I[k].enabled?'on':'off'):'—',b=k=>S[k]===undefined?null:!!S[k];
 el.innerHTML=`<div class="sh">FLIGHT</div>`+r('IFCS core',I.ifcs_core&&I.ifcs_core.active,a('ifcs_core'))+r('Wind compensation',I.ifcs_wind_compensation&&I.ifcs_wind_compensation.active,a('ifcs_wind_compensation'))+r('Command behaviour',I.ifcs_command_behaviour&&I.ifcs_command_behaviour.enabled,a('ifcs_command_behaviour'))
  +`<div class="sh">WEAPONS</div>`+r('Preset',!!MODEL.preset,MODEL.preset||'—')+r('Gimbals',WSET.gimbals,WSET.gimbals?'locked':'free')+r('Staggered fire',WSET.stagger)+r('Fallback convergence',b('fallback_convergence_distance'))+r('Precision lines',b('precision_lines'))+r('Magnified UI',b('magnified_ui'))+r('Lag pips',b('use_lag_pips'))+r('Single pips',b('use_single_pips'))+r('Fading pips',b('use_fading_pips'))}
function sys(){settings();
 const S=SH(), C=S.caps, Y=C.sys||{}, pilotSeat=seat==='pilot';
 const hasGuns=S.pilot.length||S.turrets.some(t=>t.kind!=='p')||(S.missiles||0)>0;
 const ids=['pw','en',...(Y.shield&&Y.shield.length?['sh']:[]),...(hasGuns?['wp']:[])];
 $('#pgroups').style.gridTemplateColumns=`repeat(${ids.length},1fr)`;
 $('#pgroups').innerHTML=ids.map(id=>{const g=PG[id];return `<button class="btn cut ${PGON[id]?'lit':''} ${pilotSeat?'':'ro'}" data-g="${id}">${ic(g.i)}${g.l}<small>${PGON[id]?'on':'off'}</small></button>`}).join('');
 $('#pgNote').textContent='from the game';

 // countermeasures: only the launcher types this ship carries
 const cm=$('#cmPanel'); if(cm){
  cm.innerHTML=(Y.decoy||Y.noise)?`<div class="ph">COUNTERMEASURES<span class="r">counts live</span></div>`+
   `<div class="cmrow">${Y.decoy?`<div class="cmx"><span>DECOY</span><b class="${CM.decoy!=null&&CM.decoy<10?'lo':''}">${CM.decoy??'—'}</b></div>`:''}${Y.noise?`<div class="cmx"><span>NOISE</span><b class="${CM.noise!=null&&CM.noise<10?'lo':''}">${CM.noise??'—'}</b></div>`:''}</div>`+
   (S.pilot.length?`<div class="kv">FIRE GROUP <b>${FIREGROUP}</b></div>`:'')
   :`<div class="ph">COUNTERMEASURES</div><div class="unit">No countermeasure launchers on this ship.</div>`}
 // systems from the ship's own parts; warning light + on/off live
 const A=window.AS||{}, w=k=>(A[k]==='min'||A[k]==='maj')?A[k]:'ok';
 const turretGuns=S.turrets.filter(t=>t.kind!=='p').reduce((a,t)=>a+t.mounts,0);
 const T=[
  ['i-power','POWER PLANT',true,w('PWR'),partTxt(Y.power),'PWR'],
  ['i-thr','ENGINES',true,w('THR'),`${Y.main||0} main · ${Y.mav||0} maneuvering${C.vtol?' · VTOL':''}`,'THR'],
  ...(Y.shield&&Y.shield.length?[['i-shield','SHIELDS',true,w('SHLD'),`${partTxt(Y.shield)} · ${Y.faces==='Quadrant'?'4 faces':'bubble'}`,'SHLD']]:[]),
  ['i-zone','COOLERS',true,w('COOL'),partTxt(Y.cool),'COOL'],
  ...(hasGuns?[['i-cross','WEAPONS',true,w('WPN'),[S.pilot.length?S.pilot.length+' pilot':'',turretGuns?turretGuns+' in turrets':'',S.missiles?S.missiles+' missiles':''].filter(Boolean).join(' · '),'WPN']]:[]),
  ...(Y.qd&&Y.qd.length?[['i-qt','QUANTUM DRIVE',A.QDRV!=='',w('QDRV'),partTxt(Y.qd),'QDRV']]:[]),
  ...(Y.jump&&Y.jump.length?[['i-qt','JUMP DRIVE',true,'ok',partTxt(Y.jump),'']]:[]),
  ['i-ping','RADAR',true,w('RADR'),partTxt(Y.radar),'RADR'],
  ['i-ready','LIFE SUPPORT',true,'ok',Y.life&&Y.life.length?partTxt(Y.life):'not in ship data',''],
  ['i-power','FUEL',true,'ok',`${Y.fuel||0} hydrogen · ${Y.qfuel||0} quantum tanks`,''],
  ...(Y.mineLaser&&Y.mineLaser.length?[['i-mine','MINING LASER',true,'nd',partTxt(Y.mineLaser),'']]:[]),
  ...(Y.salvHead&&Y.salvHead.length?[['i-salv','SALVAGE HEADS',true,'nd',partTxt(Y.salvHead),'']]:[]),
  ...(Y.tractorBeam&&Y.tractorBeam.length?[['i-scan','TRACTOR BEAM',true,'nd',partTxt(Y.tractorBeam),'']]:[]),
 ];
 $('#sysHead').innerHTML=`SYSTEMS · ${esc(S.short)}<span class="r">state and warnings live · parts from ship data</span>`;
 $('#sysgrid').innerHTML=T.map(s=>{const st=s[2]?s[3]:'off',lab={ok:'OK',min:'WARNING',maj:'CRITICAL',off:'OFF',nd:'NO DATA'}[st];
  return `<div class="sys cut st-${st} ${st==='maj'?'warnmaj':st==='min'?'warnmin':''}" data-sys="${s[5]}"><div class="top">${ic(s[0],'ic sm')}<span class="nm">${s[1]}</span><span class="st ${st}">${lab}</span></div><div class="part" title="${esc(s[4])}">${esc(s[4])}</div></div>`}).join('');
 const none=[!(Y.qd&&Y.qd.length)?'quantum drive':'',!(Y.shield&&Y.shield.length)?'shields':'',!hasGuns?'guns':'',!(Y.decoy||Y.noise)?'countermeasures':''].filter(Boolean);
 $('#sysNote').textContent=(none.length?'No '+none.join(', ')+' on this ship. ':'')+'No HP per part. The game sends on or off and a warning level per system.';
 const SS=SH(), wph=$('#wlist').previousElementSibling; if(wph)wph.innerHTML=bigShip?`WEAPONS · ${SS.short}<span class="r">your seat live · rest from ship data</span>`:`WEAPONS · ${SS.short}<span class="r">live from telemetry</span>`;
 const live=w=>`<div class="row sub"><span class="dot ${w[2]?(w[3]?'':'a'):'m'}"></span>${w[0]} <span class="tag">${w[1]}</span><span class="r">${w[2]?(w[3]?'ready':'not ready'):'unpowered'}</span></div>`;
 if(bigShip){const mt=myTurret(), pilotMe=!mt&&seat==='pilot';
  const pilotRow=SS.pilot.length?`<div class="row tgrp ${pilotMe?'me':''}"><span class="dot ${pilotMe?'':'m'}"></span>${pilotMe?'<b>Pilot guns</b>':'Pilot guns'} <span class="tag">${SS.pilot.length} × ${SS.pilot[0][0].replace(KW,'')} ${SS.pilot[0][1]}</span><span class="r">${pilotMe?'your seat · live':'not live'}</span></div>`+(pilotMe?SS.pilot.map(live).join(''):'')
   :`<div class="row tgrp"><span class="dot m"></span>Pilot seat <span class="tag">${SS.turrets.some(pilotTur)?'fires the slaved turret':'no guns'}</span><span class="r">${pilotMe?'your seat':''}</span></div>`;
  $('#wlist').innerHTML=pilotRow+(SS.missiles?`<div class="row tgrp"><span class="dot m"></span>Missiles <span class="tag">${SS.missiles} · ship data</span><span class="r">not in telemetry</span></div>`:'')+
   SS.turrets.map(t=>t.id===mt||(pilotMe&&pilotTur(t))?`<div class="row tgrp me"><span class="dot"></span><b>${t.n}</b> <span class="tag">${t.id===mt?'your seat':'slaved to you'}</span><span class="r">live</span></div>`+(t.id===mt?weapons:tg(t)).map(live).join('')
    :`<div class="row tgrp"><span class="dot m"></span>${t.n} <span class="tag">${t.kind==='r'?(t.ctl==='both'?'remote · pilot-slaved · ':t.ctl==='pilot'?'remote · pilot · ':'remote · '):t.kind==='x'?'missile · ':''}${t.kind==='x'?t.mounts+' × '+gunLabel(t):tLabel(t)}</span><span class="r">not live</span></div>`).join('')+
   (SS.pdc?`<div class="row tgrp"><span class="dot m"></span>Point defense <span class="tag">${SS.pdc} × automatic</span><span class="r">not in telemetry</span></div>`:'');
 }else
 if(!weapons.length)$('#wlist').innerHTML=`<div class="row unit">No guns on this ship.${SH().caps.mining?' Mining laser state is not in telemetry.':SH().caps.salvage?' Salvage head state is not in telemetry.':''}</div>`;else
 {const grp={};weapons.forEach(w=>{const k=w[0]+'|'+w[1];(grp[k]=grp[k]||{n:w[0],s:w[1],g:[]}).g.push(w)});
  $('#wlist').innerHTML=Object.values(grp).map(x=>{const rd=x.g.filter(w=>w[2]&&w[3]).length,off=x.g.filter(w=>!w[2]).length,ch=x.g.length-rd-off;
   return `<div class="row"><span class="dot ${off?'m':ch?'a':''}"></span>${x.g.length>1?x.g.length+' × ':''}${x.n} <span class="tag">${x.s}</span><span class="gdots">${x.g.map(w=>`<i class="${!w[2]?'off':w[3]?'rdy':'chg'}"></i>`).join('')}</span><span class="r">${[rd?rd+' ready':'',ch?ch+' charging':'',off?off+' off':''].filter(Boolean).join(' · ')}</span></div>`}).join('')}
}
/* ANSWER IN GAME: HUD prompts that wait for you. Each closes itself when Game.log shows the answer. */
const NTYPE={party:['i-party','Party invite','"Party Invite Received"','You have joined party'],friend:['i-friend','Friend request','"Friend Request: Add to Friends?"','Friend Added'],
 contract:['i-contract','Contract shared','"Contract Shared: <title>"','Contract Accepted with the same title'],launch:['i-party','Party launch','"Party Launching: Join Party In PU?"','Party Launch Accepted'],
 money:['i-pay','Money offered','"wants to send you … UEC"','has sent you']};
const needs=[];
/* feed groups and the notification kinds that feed them */
const FG={tip:['TIP','#9fd2de'],alert:['ALERTS','#ff5468'],msn:['MISSIONS','#5bd1e6'],money:['REWARDS','#5be69a'],people:['PEOPLE','#8fb4ff'],ship:['SHIP','#7aa3ae'],zone:['ZONES','#5f8a96']};
const OLDCAT={cbt:'alert',pay:'money',soc:'people'};
/* [time, icon, group, text, new, sub, flags] flags: aw = aUEC award (merges), chan = comms channel (SHIP only), bp = blueprint */
let feed=[];
let filter='all';

/* ---- threat strip ---- */
const PART={h:'HEAD',t:'TORSO',la:'LEFT ARM',ra:'RIGHT ARM',ll:'LEFT LEG',rl:'RIGHT LEG'};
function vitalChips(){const v=VIT(), out=[];
 if(v.hp<60)out.push([v.hp<40?'r':'a','HEALTH '+v.hp+'%']);
 if(seat==='eva'&&v.o2<40)out.push([v.o2<25?'r':'a','O₂ '+v.o2+'%']);
 const inj=INJ(), rank={sev:2,mod:1}, worst=Object.entries(inj).filter(([k,l])=>rank[l]).sort((a,b)=>rank[b[1]]-rank[a[1]])[0];
 if(worst){const more=Object.values(inj).filter(l=>rank[l]).length-1;out.push([worst[1]==='sev'?'r':'a','INJURED · '+(phone?PART[worst[0]].replace('LEFT ','L ').replace('RIGHT ','R '):PART[worst[0]])+(more?' +'+more:'')])}
 if(foot){const T=suitTempState(); if(!T.ok)out.push(['r',(T.cold?'TOO COLD ':'TOO HOT ')+outT+' °C'])}
 if(v.water<20)out.push(['a','WATER '+v.water+'%']);
 if(v.intox>0)out.push(['a','INTOXICATED '+v.intox]);
 if(v.food<20)out.push(['a','FOOD '+v.food+'%']);
 out.sort((x,y)=>(x[0]==='r'?0:1)-(y[0]==='r'?0:1));
 const lim=phone?(combat?1:2):(app.classList.contains('portrait')?3:5), shown=out.slice(0,lim), rest=out.slice(lim);
 const sh=t=>t.replace('INJURED · ','INJ ').replace('HEALTH ','HP ').replace('TOO COLD ','COLD ').replace('TOO HOT ','HOT ');
 return shown.map(([c,t])=>`<span class="chip v ${c}" data-vgo="you" data-short="${sh(t)}">${t}</span>`).join('')+(rest.length?`<span class="chip v ${rest.some(r=>r[0]==='r')?'r':'a'}" data-vgo="you">+${rest.length}</span>`:'')}
function fitThreat(){const el=document.querySelector('#threat');if(!el||!el.clientWidth)return;const over=()=>el.scrollWidth>el.clientWidth+1||[...el.children].some(c=>c.tagName==='SPAN'&&c.offsetParent&&c.scrollWidth>c.clientWidth);
 el.querySelectorAll('[data-full]').forEach(x=>{x.textContent=x.dataset.full;delete x.dataset.full});el.querySelectorAll('.zn,.zsep,.long').forEach(x=>x.style.display='');
 if(!over())return;el.querySelectorAll('.long').forEach(x=>x.style.display='none');
 if(!over())return;el.querySelectorAll('.zn,.zsep').forEach(x=>x.style.display='none');
 if(!over())return;el.querySelectorAll('[data-short]').forEach(x=>{x.dataset.full=x.textContent;x.textContent=x.dataset.short})}
function threat(){
 const el=$('#threat'),G=MODEL.log||{};
 app.classList.toggle('onfoot',foot);
 app.classList.toggle('incombat',combat);
 app.classList.toggle('gunner',seat==='gunner');
 app.classList.toggle('eva',seat==='eva');
 const place=G.place||'', zone=G.armistice===true?'ARMISTICE':G.jurisdiction?String(G.jurisdiction).toUpperCase():G.armistice===false?'NO ARMISTICE':'';
 const yw=$('#youWhere'); if(yw)yw.textContent=seat==='eva'?'EVA · outside the '+(SH().short||'ship'):'on foot'+(place?' · '+place:'');
 const gw=$('#gunWhere'); if(gw)gw.textContent=SH().short+(myTurretObj()?' · '+myTurretObj().n.toLowerCase()+' turret':'');
 const lt=$('#lsTitle'); if(lt){lt.textContent=SH().short||'—';$('#lsFull').textContent=SH().full;const hideFull=!SH().full||SH().full.endsWith(SH().short)&&SH().full.length-SH().short.length<8;$('#lsFull').style.display=hideFull?'none':'';$('#lsSep').style.display=hideFull?'none':'';$('#lsWhere').textContent=seat==='eva'?'you left it':'last ship'}
 const lsr=document.querySelector('.lastship .ph .r'); if(lsr)lsr.textContent=LASTSHIP.t?'as of '+new Date(LASTSHIP.t).toTimeString().slice(0,5)+' · you left the '+(lastSeat==='gunner'?'gunner':'pilot')+' seat':'None yet';
 const zt=$('#zoneTxt'); if(zt){zt.textContent=zone?zone.charAt(0)+zone.slice(1).toLowerCase():'';zt.className=G.armistice?'zoneA':'zoneU'}
 const nz=$('#navZone'); if(nz){nz.textContent=zone?zone.charAt(0)+zone.slice(1).toLowerCase():'—';nz.className=G.armistice?'zoneA':'zoneU'}
 if(!phone){
  el.className='threat cut'+(combat?' hot':'');
  el.innerHTML=(place?`<span class="loc">${esc(place)}</span><span class="sep zsep">·</span>`:'')+(zone?`<span class="zn">${esc(zone)}</span><span class="sep">·</span>`:'')+`<span class="sct" id="clk"></span><span class="sep">·</span><span class="pct" title="PC time"><i>PC</i><b id="pclk"></b></span>${combat&&foot?'<span class="chip r" data-short="DAMAGE">TAKING DAMAGE</span>':''}${vitalChips()}${scrHtml()}${bellHtml()}`}
 else if(combat){el.className='threat cut hot';el.innerHTML=`${foot?'<span class="chip r" data-short="DAMAGE">TAKING DAMAGE</span>':(MODEL.ann.misl==='maj'?'<span class="chip r" data-short="LOCK">MISSILE LOCK</span>':'')+(MODEL.ann.hit==='maj'?'<span class="chip a" data-short="HITS">TAKING HITS</span>':'')}${vitalChips()}${zone?`<span class="sep zsep">·</span><span class="zn">${esc(zone)}</span>`:''}${scrHtml()}${bellHtml()}`}
 else{el.className='threat cut';el.innerHTML=`${vitalChips()||'<span class="chip c">ALL CLEAR</span>'}${zone?`<span class="sep zsep">·</span><span class="zn">${esc(zone)}</span>`:''}<span class="clock" id="clk"></span>${scrHtml()}${bellHtml()}`}
 el.querySelectorAll('[data-vgo]').forEach(c=>c.onclick=()=>document.querySelector('#bezel .osb[data-p="you"]').click());
 tick();
}

/* ---- annunciators ---- */
function personalStrip(){const v=VIT(), inj=INJ(), rows=[];
 if(v.hp<60)rows.push([v.hp<40?'maj':'min','HEALTH '+v.hp+'%',v.hp<40?'CRITICAL':'LOW']);
 if(seat==='eva'&&v.o2<40)rows.push([v.o2<25?'maj':'min','OXYGEN '+v.o2+'%',v.o2<25?'CRITICAL':'LOW']);
 const word={sev:'SEVERE',mod:'MODERATE',min:'MINOR'}, rk={sev:0,mod:1,min:2};
 Object.entries(inj).filter(([k,l])=>l!=='ok').sort((a,b)=>rk[a[1]]-rk[b[1]]).forEach(([k,l])=>rows.push([l==='sev'?'maj':'min',PART[k],word[l]]));
 {const T=suitTempState(); if(!T.ok)rows.push(['maj',T.cold?'TOO COLD FOR SUIT':'TOO HOT FOR SUIT',outT+' °C']);}
 if(v.water<20)rows.push(['min','WATER '+v.water+'%','LOW']); if(v.food<20)rows.push(['min','FOOD '+v.food+'%','LOW']);
 rows.sort((a,b)=>(a[0]==='maj'?0:1)-(b[0]==='maj'?0:1));
 $('#ann').innerHTML=rows.map(r=>`<div class="${r[0]} dmg pers" data-y="1">${r[0]==='maj'?'▲ ':'● '}${r[1]} <i>${r[2]}</i></div>`).join('')+
  (rows.length?'':`<div class="ok okall" data-y="1">ALL VITALS OK · NO INJURIES</div>`);
 $$('#ann [data-y]').forEach(el=>el.onclick=()=>document.querySelector('#bezel .osb[data-p="you"]').click());
}
function ann(){
 const s={};['pwr','wpn','thr','shld','cool','qdrv','radr','prox','hit','misl'].forEach(k=>{const v=MODEL.ann[k];s[k.toUpperCase()]=v==='off'?'':v});
 window.AS=s;
 // damage strip: systems only (threat lights live in the red bar), sorted by severity then importance, OK collapsed
 const PRIO=['SHLD','PWR','THR','COOL','WPN','QDRV','RADR'], NAME={SHLD:'SHIELDS',PWR:'POWER',THR:'THRUSTERS',COOL:'COOLING',WPN:'WEAPONS',QDRV:'QUANTUM',RADR:'RADAR'};
 const sev=v=>v==='maj'?0:v==='min'?1:v===''?3:2;
 const sys=PRIO.map(k=>[k,s[k]]).sort((a,b)=>sev(a[1])-sev(b[1])||PRIO.indexOf(a[0])-PRIO.indexOf(b[0]));
 const bad=sys.filter(([k,v])=>v==='maj'||v==='min'), ok=sys.filter(([k,v])=>v==='ok'), off=sys.filter(([k,v])=>v==='');
 if(seat==='foot'||seat==='eva'){personalStrip();return}
 $('#ann').innerHTML=bad.map(([k,v])=>`<div class="${v} dmg" data-a="${k}">${v==='maj'?'▲ ':'● '}${NAME[k]} <i>${v==='maj'?'CRITICAL':'WARNING'}</i></div>`).join('')+
  (ok.length?`<div class="ok okall" data-a="">${ok.length} SYSTEMS OK</div>`:'')+(off.length?`<div class="offall">${off.map(([k])=>NAME[k]).join(' · ')} OFF</div>`:'');
 $$('#ann [data-a]').forEach(el=>el.onclick=()=>{document.querySelector('#bezel .osb[data-p="sys"]').click();const t=document.querySelector(`#sysgrid [data-sys="${el.dataset.a}"]`);if(t){t.classList.add('ping');t.scrollIntoView({block:'nearest'});setTimeout(()=>t.classList.remove('ping'),1600)}});
}

/* ---- defense ---- */
function shieldCol(p){return p<30?'#ff5468':p<60?'#ffb347':'#5bd1e6'}
const GUNSPOTS=[[84,110],[156,110],[95,104],[145,104],[114,80],[126,80],[100,112],[140,112]];
/* outline per ship: fighter / hauler / capital shape scaled to the ship's length and width.
   Stand-in until real outlines come from the game models. */
const TPL={
 fighter:{body:[[0,0],[.14,.16],[.18,.5],[.16,.9],[.1,1],[-.1,1],[-.16,.9],[-.18,.5],[-.14,.16]],wing:[[.17,.4],[1,.72],[.97,.83],[.17,.8]],nose:[[0,0],[.08,.13],[-.08,.13]],core:.72},
 hauler:{body:[[0,0],[.3,.05],[.52,.18],[.58,.85],[.48,1],[-.48,1],[-.58,.85],[-.52,.18],[-.3,.05]],wing:[[.58,.34],[1,.4],[1,.78],[.58,.84]],nose:[[0,.02],[.2,.1],[-.2,.1]],core:.7},
 capital:{body:[[0,0],[.2,.1],[.4,.32],[.5,.6],[.55,.88],[.45,1],[-.45,1],[-.55,.88],[-.5,.6],[-.4,.32],[-.2,.1]],wing:[[.5,.52],[1,.6],[1,.8],[.55,.86]],nose:[[0,0],[.12,.07],[-.12,.07]],core:.74}};
function shipTpl(k){const D=SDIM[k]||[20,15,5,[],2],a=D[0]/Math.max(D[1],1),ports=D[3].join(' '),c=(ALLSHIPS.find(x=>x.class===k)||{});
 if(D[4]>=5||a>=1.9)return 'capital';
 if(/wing/.test(ports)||(D[4]<=3&&/Combat|Racing|Exploration/i.test(c.career||'')&&!/Industrial/i.test(c.career||'')))return 'fighter';
 return 'hauler'}
function gunUV(port,tpl){const s=port||'',side=/left|_l$|_l_|port/.test(s)?-1:/right|_r$|_r_|starboard/.test(s)?1:0,under=/bottom|lower|under|chin/.test(s);
 let u=side*.3,v=.3;
 if(/nose/.test(s)){u=side*.08;v=.1}
 else if(/wing/.test(s)){u=side*(/inner/.test(s)?(tpl==='fighter'?.45:.7):/outer/.test(s)?.95:.78);v=tpl==='fighter'?.7:.58}
 else if(/body/.test(s)){u=side*.2;v=.45}
 else if(/front|fwd/.test(s)){u=side*.12;v=.16}
 else if(/center|centre|spine|top|upper|bottom|lower/.test(s)&&!side){u=0;v=.3}
 else if(/top|upper|bottom|lower/.test(s)){u=side*.2;v=.32}
 return [u,v,under]}
function shipGeo(k){const D=SDIM[k]||[20,15,5,[],2],SS=SHAPE[k],a=Math.max(.6,SS?SS[0]/Math.max(SS[1],1):D[0]/Math.max(D[1],1)),tpl=shipTpl(k),T=TPL[tpl];
 const rx=100;let hs=rx*.56,H=2*hs*a,ry=Math.max(rx*.92,H/2/.72);if(ry>rx*1.6){ry=rx*1.6;H=2*ry*.72;hs=H/(2*a)}
 const bubS=((SHIPS[k]||{}).caps||{}).sys&&SHIPS[k].caps.sys.faces!=='Quadrant',SM=bubS?30:57;
 let u=1,vbH=236,W2=280;for(let i=0;i<5;i++){vbH=2*ry+68*u;W2=2*(rx+SM*u);u=Math.max(W2/280,vbH/236)}vbH=Math.round(vbH);W2=Math.round(W2);
 const cx=W2/2,cy=vbH/2,y0=cy-H/2;
 const X=q=>cx+q*hs,Y=v=>y0+v*H,pts=L=>L.map(([q,v])=>`${X(q).toFixed(1)},${Y(v).toFixed(1)}`).join(' ');
 const mir=L=>L.map(([q,v])=>[-q,v]);
 const MX=m=>cx+m/(SS?SS[1]/2:1)*hs, MY=m=>cy-m/(SS?SS[0]/2:1)*(H/2), HP=n=>SS&&SS[3][(n||'').toLowerCase().replace(/^hardpoint_/,'')];
 const used=[];const guns=(D[3]||[]).map(pt=>{const e=HP(pt);if(e){const x=MX(e[0]),y=MY(e[1]);used.push([x,y]);return [x,y,false,true]}let[q,v,under]=gunUV(pt,tpl);let x=X(q),y=Y(v);while(used.some(([ux,uy])=>Math.hypot(ux-x,uy-y)<9*u))y+=9*u;used.push([x,y]);return [x,y,under]});
 // pilot guns in turrets the pilot fires (no gunner seat): place them at the turret on the game model
 {const pc=((SHIPS[k]||{}).caps||{}).pilotTurrets||[],nm=q=>(q||'').toLowerCase().replace(/^hardpoint_/,''),have=new Set((D[3]||[]).map(nm));let miss=((SHIPS[k]||{}).pilot||[]).length-guns.length;
  for(const [hp,n] of pc){if(miss<=0)break;if(have.has(nm(hp)))continue;const e=HP(hp);if(!e)continue;for(let i=0;i<n&&miss>0;i++,miss--)guns.push([MX(e[0])+(i-(n-1)/2)*8*u,MY(e[1]),false,true])}}
 const real=!!SS, ol=SS?SS[2].map(([x,y])=>`${MX(x).toFixed(1)},${MY(y).toFixed(1)}`).join(' '):null;
 return {k,tpl,a,W:W2,vbH,cx,cy,rx,ry,hs,H,y0,u,X,Y,real,turXY:id=>{const e=HP(id);return e?[MX(e[0]),MY(e[1])]:null},
  body:ol||pts(T.body),wl:ol?'':pts(mir(T.wing)),wr:ol?'':pts(T.wing),nose:ol?'':pts(T.nose),core:[cx,ol?cy+H*.08:Y(T.core)],
  eng:[[X(-.12),Y(.95)],[X(.04),Y(.95)]],guns,
  ref:(x,y)=>[cx+(x-120)/48*hs,y0+(y-50)/108*H]}}
const shCol=p=>p==null?'#5f8a96':p<30?'#ff5468':p<60?'#ffb347':'#5bd1e6';
function drawDefense(f,hull,bubble){const G=shipGeo(shipK),sv=$('#defsvg');if(!sv)return G;sv.setAttribute('viewBox',`0 0 ${G.W} ${G.vbH}`);sv.innerHTML=defMarkup(G,f,hull,bubble,true,'hullclip');return G}
function defMarkup(G,f,hull,bubble,full,cid){const{cx,cy,rx,ry,u}=G;const pc=v=>v==null?'—':v+'%';
 const P=t=>[(cx+rx*Math.cos(t)).toFixed(1),(cy+ry*Math.sin(t)).toFixed(1)];
 const arc=(t1,t2)=>{const a=P(t1),b=P(t2);return `M${a[0]} ${a[1]} A${rx} ${ry} 0 0 1 ${b[0]} ${b[1]}`};
 const Q={F:-Math.PI/2,R:0,B:Math.PI/2,L:Math.PI},half=40*Math.PI/180,sw=13*u;
 let arcs='';for(const k of 'FRBL'){const c=Q[k],p=Math.max(0,Math.min(100,f[k]))/100;
  arcs+=`<path d="${arc(c-half,c+half)}" stroke="#12303a" stroke-width="${sw}" fill="none"/>`;
  if(p>0)arcs+=`<path id="f${k}" d="${arc(c-half*p,c+half*p)}" stroke="${shCol(f[k])}" stroke-width="${sw}" fill="none" style="filter:drop-shadow(0 0 ${3*u}px ${shCol(f[k])}66)"/>`}
 const lab=(k,x,y,txt,col)=>`<text x="${x}" y="${y}" text-anchor="middle" font-size="${15*u}" font-weight="700" fill="${col}">${txt}</text>`;
 let labs='';
 if(!full);else if(bubble){labs+=lab('F',cx,cy-ry-sw-2*u,pc(f.F),shCol(f.F))}
 else labs+=lab('F',cx,cy-ry-sw-2*u,pc(f.F),shCol(f.F))+lab('B',cx,cy+ry+sw+14*u,pc(f.B),shCol(f.B))+lab('R',cx+rx+sw+18*u,cy+5*u,pc(f.R),shCol(f.R))+lab('L',cx-rx-sw-18*u,cy+5*u,pc(f.L),shCol(f.L));
 const hc=hull==null?'#5f8a96':hull<35?'#ff5468':hull<70?'#ffb347':'#5be69a',yf=hull==null?G.vbH:G.y0+G.H*(1-hull/100);
 const parts=G.real?`<polygon points="${G.body}"/>`:`<polygon points="${G.wl}"/><polygon points="${G.wr}"/><polygon points="${G.body}"/>`;
 if(!full)return `<defs><clipPath id="${cid}">${parts}</clipPath></defs><g fill="#081820">${parts}</g><g clip-path="url(#${cid})"><rect x="0" y="${yf.toFixed(1)}" width="${G.W}" height="${G.vbH}" fill="${hc}" opacity=".5"/></g><g fill="none" stroke="${hc}" stroke-width="${2.4*u}" stroke-linejoin="round">${parts}</g>${arcs.replace(/stroke-width="[\d.]+"/g,`stroke-width="${18*u}"`)}`;
 return `<defs><clipPath id="hullclip">${parts}</clipPath></defs>
  <g class="hullbase" fill="#081820">${parts}</g>
  <g clip-path="url(#hullclip)"><rect x="0" y="${yf.toFixed(1)}" width="${G.W}" height="${G.vbH}" fill="${hc}" opacity=".5"/><line x1="0" x2="${G.W}" y1="${yf.toFixed(1)}" y2="${yf.toFixed(1)}" stroke="${hc}" stroke-width="${1.6*u}"/></g>
  <g id="shipg" fill="none" stroke="${hc}" stroke-width="${1.4*u}" stroke-linejoin="round">
   ${G.real?`<polygon id="sBody" points="${G.body}"/><g clip-path="url(#${cid||'hullclip'})" stroke="none" fill="transparent"><rect class="zband" id="sNose" x="0" y="${G.y0}" width="${G.W}" height="${G.H*.14}"/><rect class="zband" id="sWL" x="0" y="${G.y0+G.H*.3}" width="${G.cx-G.hs*.35}" height="${G.H*.55}"/><rect class="zband" id="sWR" x="${G.cx+G.hs*.35}" y="${G.y0+G.H*.3}" width="${G.W}" height="${G.H*.55}"/></g>`:`<polygon id="sWL" points="${G.wl}"/><polygon id="sWR" points="${G.wr}"/><polygon id="sBody" points="${G.body}"/><polygon id="sNose" points="${G.nose}" stroke-width="${1*u}"/>`}
   <circle id="sCore" cx="${G.core[0]}" cy="${G.core[1].toFixed(1)}" r="${6*u}"/><circle id="sQt" cx="${G.core[0]}" cy="${G.core[1].toFixed(1)}" r="${10*u}" stroke="#2b6b80" stroke-dasharray="${3*u} ${3*u}"/>
   ${G.real?`<g clip-path="url(#${cid||'hullclip'})" stroke="none" fill="transparent"><rect class="zband" id="sEng" x="0" y="${G.y0+G.H*.88}" width="${G.W}" height="${G.H*.12}"/></g>`:`<path id="sEng" d="${G.eng.map(([x,y])=>`M${x.toFixed(1)} ${y.toFixed(1)} h${(G.hs*.08).toFixed(1)} v${(G.H*.05).toFixed(1)} h${(-G.hs*.08).toFixed(1)}z`).join(' ')}"/>`}
  </g>
  <text x="${4*u}" y="${G.vbH-26*u}" font-size="${10*u}" letter-spacing=".16em" fill="#9fd2de">HULL</text>
  <text x="${4*u}" y="${G.vbH-6*u}" font-size="${21*u}" font-weight="700" fill="${hc}">${pc(hull)}</text>
  ${(()=>{const w=weapons||[];if(!w.length)return '';const rd=w.filter(x=>x[2]&&x[3]).length,gc=rd===w.length?'#5be69a':rd?'#ffb347':'#ff5468';return `<text x="${G.W-4*u}" y="${G.vbH-26*u}" text-anchor="end" font-size="${10*u}" letter-spacing=".16em" fill="#9fd2de">GUNS</text><text x="${G.W-4*u}" y="${G.vbH-6*u}" text-anchor="end" font-size="${21*u}" font-weight="700" fill="${gc}">${rd}/${w.length}</text>`})()}
  <g id="gunmarks"></g>${arcs}${labs}`}
function gunMarks(){const g=$('#gunmarks');if(!g)return;const G=window.DG||shipGeo(shipK),U=G.u,gs=G.guns;const lt0=$('#layoutTag');if(lt0)lt0.textContent='';
 if(bigShip){const S=SH(), mt=myTurret(), pilotMe=!mt&&seat==='pilot';
  const pg=S.pilot.map((w,i)=>{const[x,y]=gs[i]||G.ref(...GUNSPOTS[i%GUNSPOTS.length]),st=pilotMe?(!w[2]?'off':w[3]?'rdy':'chg'):'crew';
   return `<rect class="gm ${st}" x="${x-3.5*U}" y="${y-3.5*U}" width="${7*U}" height="${7*U}"><title>Pilot gun · ${esc(w[0])} ${w[1]}${pilotMe?'':' · not live'}</title></rect>`}).join('');
  const P=placeAll(shipK), cand=candidates(), picked=!!turretPick[shipK];
  const pdcs=Array.from({length:S.pdc||0},(_,i)=>{const[x,y]=G.ref(120+(i%2?7:-7),58+i*(92/Math.max(1,S.pdc)));return `<circle class="pdc" cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="${1.6*U}"><title>Point defense · automatic · not in telemetry</title></circle>`}).join('');
  g.innerHTML=pg+pdcs+S.turrets.map(t=>{const{xy:[x0,y0],under}=P[t.id],[x,y]=G.turXY(t.id)||G.ref(x0,y0),me=t.id===mt,maybe=!picked&&cand.length>1&&cand.includes(t);
   const mineP=pilotMe&&pilotTur(t),tw=mineP?tg(t):weapons;
   const st=(me&&(picked||cand.length<2))||mineP?(tw.some(w=>!w[2])?'off':tw.every(w=>w[3])?'rdy':'chg'):maybe?'cand':'crew';
   const r6=6*U,r5=5*U,shape=t.kind==='r'?`<path d="M${x} ${y-r6} L${x+r6} ${y} L${x} ${y+r6} L${x-r6} ${y}Z"/>`:t.kind==='x'?`<path d="M${x} ${y-r6} L${x+r6} ${y+r5} L${x-r6} ${y+r5}Z"/>`:`<circle cx="${x}" cy="${y}" r="${r5}"/>`;
   return `<g class="tm ${st} ${under?'under':''}" data-tpick="${t.id}" style="cursor:pointer">${shape}${st!=='crew'&&st!=='cand'?`<circle class="tmr" cx="${x}" cy="${y}" r="${9.5*U}"/>`:''}<title>${t.n} · ${t.kind==='r'?(t.ctl==='both'?'remote · pilot, or a gunner':t.ctl==='pilot'?'remote · pilot':'remote · gunner'):t.kind==='x'?'missile':t.kind==='u'?'turret':'manned'} · ${t.kind==='x'?t.mounts+' × '+t.gn:tLabel(t)}${under?' · underside':''}</title></g>`}).join('');
  g.querySelectorAll('[data-tpick]').forEach(e=>e.onclick=()=>{if(seat!=='gunner'||(SH().turrets.find(x=>x.id===e.dataset.tpick)||{}).ctl==='pilot')return;turretPick[shipK]=e.dataset.tpick;render()});
  const lt=$('#layoutTag'); if(lt)lt.textContent=G.real?'from the game model':LAYOUT[shipK]?'hand-made layout':'layout from turret names';
  return}
 const lt2=$('#layoutTag'); if(lt2)lt2.textContent=G.real?'from the game model':'shape from ship size';
 g.innerHTML=weapons.map((w,i)=>{const[x,y,und]=gs[i]||G.ref(...GUNSPOTS[i%GUNSPOTS.length]),st=!w[2]?'off':w[3]?'rdy':'chg';
  return `<rect class="gm ${st}${und?' under':''}" x="${x-3.5*U}" y="${y-3.5*U}" width="${7*U}" height="${7*U}"><title>${esc(w[0])} ${w[1]} · ${st==='off'?'unpowered':st==='rdy'?'ready':'not ready'}</title></rect>`}).join('')}
/* damaged parts only, under the ship (T annunciators, shields, hull, weapons) */
function dlist(f,hull,bubble){const el=$('#dlist');if(!el)return;const A=window.AS||{},rows=[];
 if(HITS.n)rows.push([Date.now()-HITS.t<4000?'maj':'min',Date.now()-HITS.t<4000?'TAKING HITS':'HITS TAKEN',HITS.n+(HITS.n===1?' burst':' bursts')]);
 const wf=Object.entries(f).sort((a,b)=>a[1]-b[1])[0];if(wf[1]!=null&&wf[1]<60)rows.push(['maj',bubble?'SHIELDS':'SHIELDS · '+{F:'front',R:'right',L:'left',B:'rear'}[wf[0]],wf[1]+'%']);
 if(hull!=null&&hull<70)rows.push([hull<35?'maj':'min','HULL',hull+'%']);
 for(const[k,n] of [['THR','THRUSTERS'],['PWR','POWER PLANT'],['COOL','COOLERS'],['QDRV','QUANTUM DRIVE'],['RADR','RADAR'],['WPN','WEAPONS']])if(A[k]==='maj'||A[k]==='min')rows.push([A[k],n,A[k]==='maj'?'critical':'warning']);
 const w=weapons||[],rd=w.filter(x=>x[2]&&x[3]).length;
 const gun=w.length?`<div class="dr2 gun"><span>GUNS <i class="gdots">${w.map(x=>`<i class="${!x[2]?'off':x[3]?'rdy':'chg'}"></i>`).join('')}</i></span><b>${rd}/${w.length} ready</b></div>`:'';
 const nod=hull==null&&Object.values(f).every(v=>v==null)?`<div class="dr2 nd"><span>HULL · SHIELDS</span><b>not sent by this game build</b></div>`:'';
 el.innerHTML=nod+(rows.length?rows.map(([c,n,v])=>`<div class="dr2 ${c}"><span>${c==='maj'?'▲':'●'} ${n}</span><b>${v}</b></div>`).join(''):`<div class="dr2 ok"><span>✓ All systems OK</span><b></b></div>`)+gun}
/* instrument cluster (right column): T speed, heading, alt, VSI, g_meter, fuel, boost, IFCS, power, environment; S speed limits */
let SNOW=0,GNOW=1;
function hud(){const el=document.getElementById('hudsvg');if(!el||!el.getClientRects().length)return;
 const bb=el.getBoundingClientRect(),HV=Math.max(742,Math.round(300*bb.height/Math.max(1,bb.width))),GP=Math.max(6,(HV-692-6)/5);
 el.setAttribute('viewBox',`0 0 300 ${HV}`);
 const YD=66+GP,CY=YD+126,YR=YD+252+GP,YT=YR+64+GP,YL=YT+129+GP+10,YO=YL-10+77+GP;
 const NS=(t,a,i='')=>`<${t} ${Object.entries(a).map(([k,v])=>`${k}="${v}"`).join(' ')}>${i}</${t}>`;
 const P=(cx,cy,r,a)=>[cx+r*Math.cos(a*Math.PI/180),cy+r*Math.sin(a*Math.PI/180)];
 const ARC=(cx,cy,r,a0,a1)=>{const[x0,y0]=P(cx,cy,r,a0),[x1,y1]=P(cx,cy,r,a1);return `M${x0.toFixed(1)} ${y0.toFixed(1)} A${r} ${r} 0 ${a1-a0>180?1:0} 1 ${x1.toFixed(1)} ${y1.toFixed(1)}`};
 const C={c:'#5bd1e6',c2:'#2b6b80',g:'#5be69a',a:'#ffb347',r:'#ff5468',v:'#b58cff',dim:'#7aa3ae',mute:'#4f6f78',hi:'#eafcff',bg:'#081820',line:'#1f4c5e'};
 const T=(x,y,t,o={})=>NS('text',{x,y,'font-size':o.s||10,fill:o.f||C.dim,'text-anchor':o.a||'middle','font-weight':o.w||400,'letter-spacing':o.ls||'.06em','font-family':o.m?'ui-monospace,Consolas,monospace':'inherit'},t);
 const glow=c=>`style="filter:drop-shadow(0 0 4px ${c})"`;
 const[SCM,BST,NAV]=SPDS(),V=SNOW,H=Math.round(((HDG%360)+360)%360),MF=MODEL.flight,BOOST=(MF.boost||{}).reserves==null?null:MF.boost.reserves/100,FV={alt:MF.alt==null?null:Math.round(MF.alt),vsi:MF.vsi==null?null:Math.round(MF.vsi)};
 let o=`<defs><linearGradient id="hTank" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#0a2a33"/><stop offset=".5" stop-color="#103a46"/><stop offset="1" stop-color="#0a2a33"/></linearGradient><radialGradient id="hFace"><stop offset="0" stop-color="#0b2430"/><stop offset="1" stop-color="#061219"/></radialGradient></defs>`;
 o+=T(6,14,'FLIGHT',{a:'start',s:10,ls:'.18em'})+NS('rect',{x:238,y:3,width:58,height:15,fill:'#0f2f3a',stroke:C.c})+T(267,14,STATE.master?STATE.master.toUpperCase():'—',{f:C.c,w:700,s:10})+(MODEL.flight.masterGuess?`<title>From the shield and quantum drive lights</title>`:'');
 o+=NS('rect',{x:6,y:26,width:288,height:34,fill:C.bg,stroke:C.line});
 for(let dd=Math.ceil((H-60)/5)*5;dd<=H+60;dd+=5){const x=150+(dd-H)*2.3;if(x<10||x>290)continue;const d=((dd%360)+360)%360,maj=d%15===0;o+=NS('line',{x1:x,y1:60,x2:x,y2:maj?50:55,stroke:maj?C.c:C.c2});
  if(maj&&Math.abs(x-150)>22){const L={0:'N',90:'E',180:'S',270:'W'};o+=T(x,44,L[d]??String(d).padStart(3,'0'),{f:L[d]!=null?C.a:C.dim,s:10})}}
 o+=NS('path',{d:'M150 60 L144 66 L156 66Z',fill:C.c})+NS('rect',{x:128,y:29,width:44,height:19,fill:'#061219',stroke:C.c})+T(150,43,String(H).padStart(3,'0')+'°',{f:C.hi,w:700,s:12,m:1});
 const cx=150,cy=CY,R=112,A0=135,A1=405,ang=v=>A0+(A1-A0)*Math.sqrt(Math.min(1,Math.max(0,v)/NAV));
 o+=NS('circle',{cx,cy,r:R+14,fill:'url(#hFace)',stroke:C.line});
 o+=NS('path',{d:ARC(cx,cy,R,A0,ang(SCM)),stroke:'#12404d','stroke-width':9,fill:'none'})+NS('path',{d:ARC(cx,cy,R,ang(SCM),ang(BST)),stroke:'#3a2e10','stroke-width':9,fill:'none'})+NS('path',{d:ARC(cx,cy,R,ang(BST),A1),stroke:'#2a1d40','stroke-width':9,fill:'none'});
 const vc=V<=SCM+.5?C.c:V<=BST?C.a:C.v;if(V>0)o+=`<path d="${ARC(cx,cy,R,A0,Math.max(A0+.5,ang(V)))}" stroke="${vc}" stroke-width="9" fill="none" ${glow(vc)}/>`;
 const ticks=[0,25,50,75,100,125,150,175,200,250,300,350,400,450,500,600,700,800,900,1000,1100,1200,1400,1600,1800,2000].filter(v=>v<=NAV),majors=NAV>1500?[0,200,400,800,1200,1600,2000]:[0,100,200,400,600,800,1000];
 for(const v of ticks){const a=ang(v),mj=majors.includes(v),[x0,y0]=P(cx,cy,R-8,a),[x1,y1]=P(cx,cy,R-(mj?20:14),a);o+=NS('line',{x1:x0,y1:y0,x2:x1,y2:y1,stroke:mj?C.hi:C.c2,'stroke-width':mj?1.6:1});if(mj){const[lx,ly]=P(cx,cy,R-32,a);o+=T(lx,ly+3.5,v,{s:9,m:1})}}
 for(const[v,l,c] of [[SCM,'SCM',C.c],...(BST<NAV?[[BST,'BST',C.a]]:[]),[NAV,BST<NAV?'NAV':'BST·NAV',C.v]]){const a=ang(v),[x0,y0]=P(cx,cy,R+6,a),[x1,y1]=P(cx,cy,R-6,a),[lx,ly]=P(cx,cy,R+20,a);o+=NS('line',{x1:x0,y1:y0,x2:x1,y2:y1,stroke:c,'stroke-width':2.5})+T(lx,ly+3,l,{f:c,s:8.5,w:700})}
 {const[nx,ny]=P(cx,cy,R-4,ang(V)),[bx,by]=P(cx,cy,-14,ang(V));o+=`<line x1="${bx.toFixed(1)}" y1="${by.toFixed(1)}" x2="${nx.toFixed(1)}" y2="${ny.toFixed(1)}" stroke="${C.hi}" stroke-width="3" stroke-linecap="round" ${glow(C.c)}/>`+NS('circle',{cx,cy,r:5,fill:'#0b2430',stroke:C.c,'stroke-width':2})}
 o+=T(cx,cy+62,V,{f:C.hi,s:34,w:700,m:1})+T(cx,cy+80,'m/s',{s:9,ls:'.16em'})+T(cx,cy+104,`SCM ${SCM} · BST ${BST} · NAV ${NAV.toLocaleString()}`,{s:8.5});
 const box=(x,y,w,l,val,unit,col,sc)=>{let s=NS('rect',{x,y,width:w,height:64,fill:C.bg,stroke:C.line})+T(x+6,y+13,l,{a:'start',s:8.5,ls:'.16em'})+T(x+w-6,y+36,val,{a:'end',f:col,s:19,w:700,m:1})+T(x+w-6,y+48,unit,{a:'end',s:8.5});
  const sx=x+6,sw=w-12,sy=y+55;s+=NS('rect',{x:sx,y:sy,width:sw,height:4,fill:'#12262c'});for(const[z0,z1,c] of sc.z)s+=NS('rect',{x:sx+sw*z0,y:sy,width:sw*(z1-z0),height:4,fill:c});return sc.v==null?s:s+`<path d="M${(sx+sw*sc.v).toFixed(1)} ${sy-2} l-4 -5 h8z" fill="${C.hi}"/>`};
 const fr=(v,a,b)=>Math.max(0,Math.min(1,(v-a)/(b-a)));
 o+=box(6,YR,92,'ALT',FV.alt==null?'—':FV.alt.toLocaleString(),FV.alt==null?'no body':'m',FV.alt==null?C.mute:C.hi,{v:FV.alt==null?null:fr(FV.alt,0,20000),z:[[0,1,'#12404d']]})
  +box(104,YR,92,'VSI',FV.vsi==null?'—':(FV.vsi<0?'▼ ':FV.vsi>0?'▲ ':'')+Math.abs(FV.vsi),'m/s',FV.vsi==null?C.mute:FV.vsi<0?C.a:C.hi,{v:FV.vsi==null?null:fr(FV.vsi,-50,50),z:[[0,.5,'#3a2e10'],[.5,1,'#12404d']]})
  +box(202,YR,92,'G',GNOW==null?'—':GNOW.toFixed(1),MF.gMax?'of '+Math.round(MF.gMax):'',GNOW==null?C.mute:GNOW>12?C.a:C.hi,{v:GNOW==null?null:fr(GNOW,0,20),z:[[0,.6,'#12404d'],[.6,.95,'#3a2e10'],[.95,1,'#4a1018']]});
 const tank=(x,y,w,h,l,id,p0,c)=>{const p=p0==null?0:p0;let s=NS('rect',{x,y,width:w,height:h,rx:w/2,fill:'url(#hTank)',stroke:C.c2,'stroke-width':1.5});const fh=(h-8)*p,top=y+4+(h-8)-fh;
  s+=`<clipPath id="hc${id}"><rect x="${x+4}" y="${y+4}" width="${w-8}" height="${h-8}" rx="${(w-8)/2}"/></clipPath><rect x="${x}" y="${top}" width="${w}" height="${fh}" fill="${c}" opacity=".55" clip-path="url(#hc${id})"/><line x1="${x+4}" x2="${x+w-4}" y1="${top}" y2="${top}" stroke="${c}" stroke-width="2" ${glow(c)}/>`;
  for(const[q,lb] of [[1,'F'],[.75,''],[.5,'½'],[.25,''],[0,'E']]){const yy=y+4+(h-8)*(1-q);s+=NS('line',{x1:x+w,y1:yy,x2:x+w+6,y2:yy,stroke:C.c2})+(lb?T(x+w+13,yy+3.5,lb,{s:9}):'')}
  if(p0==null)s+=T(x+w/2,y+h/2+3,'NO',{f:C.mute,s:8.5,w:700,ls:'.12em'})+T(x+w/2,y+h/2+14,'DATA',{f:C.mute,s:8.5,w:700,ls:'.12em'});
  return s+T(x+w/2,y+h+16,l,{s:9,ls:'.16em'})+T(x+w/2,y+h+31,p0==null?'—':Math.round(p*100)+'%',{f:p0==null?C.mute:C.hi,s:12,w:700,m:1})};
 o+=tank(22,YT,46,96,'H₂','h2',MF.h2==null?null:MF.h2/100,C.c)+tank(112,YT,46,96,'QT','qt',MF.qt==null?null:MF.qt/100,'#8fb4ff');
 {const x=210,y=YT,n=10,low=BOOST!=null&&BOOST<.2,bc=low?C.r:C.a;for(let i=0;i<n;i++){const on=i<(BOOST==null?0:Math.max(1,Math.round(BOOST*n))),yy=y+96-(i+1)*9.6;o+=`<rect x="${x}" y="${yy.toFixed(1)}" width="56" height="7.6" fill="${on?bc:'#13262c'}" stroke="${on?bc:C.line}" ${on?glow(bc):''}/>`}
  o+=T(x+28,y+112,'BOOST',{s:9,ls:'.16em'})+T(x+28,y+127,(BOOST==null?'—':Math.round(BOOST*100)+'%')+(low?' · LOW':''),{f:low?C.r:C.hi,s:12,w:700,m:1})}
 const grid=(x,y,l,items)=>{let s=T(x,y-6,l,{a:'start',s:8.5,ls:'.18em'});items.forEach(([n,st,tt],i)=>{const cx=x+(i%3)*48,cy=y+Math.floor(i/3)*23,col=st==='on'?C.g:st==='warn'?C.a:st==='bad'?C.r:st==='en'?C.c2:'#22343a';
  s+=`<g><title>${tt}</title><rect x="${cx}" y="${cy}" width="44" height="19" rx="2" fill="${st==='on'?'rgba(91,230,154,.18)':st==='warn'?'rgba(255,179,71,.22)':st==='bad'?'rgba(255,84,104,.25)':'#0a161b'}" stroke="${col}" ${st==='on'||st==='warn'||st==='bad'?glow(col):''}/>`+T(cx+22,cy+13,n,{f:st==='off'?C.mute:col,s:8,w:700,ls:'.02em'})+'</g>'});return s};
 const AB={COUPLED:'COUPLED',ESP:'ESP','G-SAFE':'G-SAFE',CRUISE:'CRUISE',LIMITER:'LIMIT',PROX:'PROX',LANDING:'LAND','GRAV COMP':'G-COMP',STABILITY:'STAB'};
 o+=grid(6,YL,'IFCS',Object.entries(IFCS()).map(([n,[en,ac]])=>[AB[n]||n,ac?'on':en?'en':'off',n.toLowerCase()+' · '+(ac?'active':en?'on':'off')]));
 const A=window.AS||{},PW=[['PLANT','PWR','power plants'],['ENGINE','THR','engines'],['SH GEN','SHLD','shield generators'],['SH EMIT','SHLD','shield emitters'],['WEAPON','WPN','weapons'],['COOLER','COOL','coolers'],['SENSOR','RADR','sensors'],['LIFE','','life support'],['GRAV','','gravity generators']];
 o+=grid(154,YL,'POWER',PW.map(([n,k,tt])=>[n,A[k]==='maj'?'bad':A[k]==='min'?'warn':'on',tt]));
 const E=envData(),[vcl,vtx]=envVerdict(E),RT=suitRating(true);
 o+=NS('line',{x1:6,y1:YO,x2:294,y2:YO,stroke:C.line})+T(6,YO+16,'CABIN',{a:'start',s:9,ls:'.18em'})
  +T(294,YO+16,'outside '+(MF.density==null?'—':MF.density<0.01?'vacuum':MF.density.toFixed(2)+' kg/m³'),{a:'end',s:9,f:MF.density!=null&&MF.density<0.01?C.a:C.dim});
 o+=box(6,YO+24,92,'TEMP',E.temp==null?'—':String(E.temp).replace('-','−'),(RT.tmin==null?'°C':`°C · suit ${RT.tmin}`),E.temp==null?C.mute:E.temp<RT.tmin||E.temp>RT.tmax?C.r:C.g,{v:E.temp==null?null:fr(E.temp,-100,100),z:[[0,fr(RT.tmin,-100,100),'#2a1d40'],[fr(RT.tmin,-100,100),fr(RT.tmax,-100,100),'#173d2a'],[fr(RT.tmax,-100,100),1,'#4a1018']]})
  +box(104,YO+24,92,'PRESS',E.press.toFixed(2),'atm',E.press<.5?C.r:C.hi,{v:fr(E.press,0,2),z:[[0,.25,'#4a1018'],[.25,1,'#12404d']]});
 const lb=(x,y,t,c,lit)=>`<rect x="${x}" y="${y}" width="90" height="19" fill="${lit?c+'33':'#0a161b'}" stroke="${lit?c:C.line}" ${lit?glow(c):''}/>`+T(x+45,y+13.5,t,{f:lit?c:C.mute,s:9,w:700,ls:'.12em'});
 o+=lb(204,YO+24,E.breath?'AIR OK':'NO AIR',E.breath?C.g:C.r,true)+lb(204,YO+46,vcl==='ok'?'HELMET OFF OK':'HELMET ON',vcl==='ok'?C.g:vcl==='warn'?C.a:C.r,true)+lb(204,YO+68,E.rad>.5?'RADIATION':'RAD',E.rad>.5?C.r:C.g,E.rad>.5)+T(249,YO+102,E.grav.toFixed(1)+' g',{s:9});
 o+=`<title>${vtx}</title>`;
 el.innerHTML=o}
/* combat view: on with a missile lock or hits (T annunciators), back to calm 1 minute after the last one.
   ◂ MAP VIEW leaves it by hand until the next engagement. */
const CV={on:false,last:0,sup:false,peek:false,manual:false};
function cviewSync(){const A=window.AS||{},threat=combat&&(A.MISL==='maj'||A.HIT==='maj'),now=Date.now();
 if(threat){CV.last=now;if(!CV.sup)CV.on=true}else{CV.sup=false;if(CV.on&&!CV.manual&&now-CV.last>60000)CV.on=false}
 const on=CV.on&&!phone&&!foot;app.classList.toggle('cview',on);if(!on)CV.peek=false;app.classList.toggle('mappeek',on&&CV.peek);return on}
setInterval(()=>{const was=app.classList.contains('cview'),on=cviewSync();if(was!==on)render();else if(on)cviewHead()},1000);
function cviewHead(){const dn=$('#defName');if(!dn)return;const r=$('.defense .ph .r');
 if(!app.classList.contains('cview')){dn.textContent='DEFENSE · '+SH().short.toUpperCase();
  if(r&&!phone&&!foot){if(!r.querySelector('#cvGo'))r.innerHTML='<button class="mapback" id="cvGo" type="button">COMBAT VIEW ▸</button>'}
  else if(r&&!r.querySelector('#layoutTag'))r.innerHTML='<span id="layoutTag"></span> · 5.0';return}
 const A=window.AS||{},threat=combat&&(A.MISL==='maj'||A.HIT==='maj'),left=Math.max(0,60-Math.floor((Date.now()-CV.last)/1000));
 dn.innerHTML=`DEFENSE · ${esc(SH().short.toUpperCase())} · COMBAT${threat||CV.manual?'':` <span class="cvcal">calm in ${Math.floor(left/60)}:${String(left%60).padStart(2,'0')}</span>`}`;
 if(r&&!r.querySelector('#mapBack'))r.innerHTML='<button class="mapback" id="mapBack" type="button">◂ MAP VIEW</button>'}
document.addEventListener('click',e=>{if(e.target.closest('#mapBack')){CV.on=false;CV.sup=true;CV.manual=false;render();return}
 if(e.target.closest('#cvGo')){CV.on=true;CV.manual=true;CV.sup=false;CV.last=Date.now();render();return}
 if(e.target.closest('#mapDrawer')){CV.peek=!CV.peek;app.classList.toggle('mappeek',CV.peek);const b=$('#mapDrawer b');if(b)b.textContent=CV.peek?'◂':'▸';setTimeout(()=>{drawMap&&drawMap();trimDist()},280)}});
function cview(){if(!app.classList.contains('cview'))return;const A=window.AS||{},S=SH();
 const lamp=(t,st,blink)=>`<span class="lmp ${st}${blink?' blink':''}">${t}</span>`,lv=v=>v==='maj'?'r':v==='min'?'a':v==='ok'?'g':'off';
 $('#cAnn').innerHTML=lamp('MISSILE LOCK',A.MISL==='maj'?'r':'off',A.MISL==='maj')+lamp('HIT',A.HIT==='maj'?'r':A.HIT==='min'?'a':'off')+lamp('PROX',A.PROX==='maj'?'r':A.PROX==='min'?'a':'off')+'<i></i>'
  +[['PWR','PWR'],['WPN','WPN'],['THR','THR'],['SHLD','SHLD'],['COOL','COOL'],['QDRV','QDRV'],['RADR','RADR']].map(([k,t])=>lamp(t,lv(A[k]))).join('');
 const ports=(SDIM[shipK]||[])[3]||[],mt=myTurretObj&&myTurretObj(),w=weapons||[];
 const mount=i=>mt?mt.n.toLowerCase():(ports[i]||'').replace(/^(weapon|gun)s?_?/,'').replace(/_/g,' ').replace(/\b(class\d|s\d)\b/g,'').trim()||'mount';
 const tile=(x,i)=>{const st=!x[2]?'off':x[3]?'rdy':'chg';return `<div class="gun ${st}"><b>${x[1]||''}</b><span>${esc(x[0])}<small>${esc(mount(i))}</small></span><i>${st==='rdy'?'READY':st==='chg'?'CHARGING':'OFF'}</i></div>`};
 let wl='';if(!w.length)wl=`<div class="unit">${seat==='pilot'&&bigShip?'Pilot seat · no guns. Turrets are crewed.':'No guns on this seat.'}</div>`;
 else if(w.length<=6)wl=w.map(tile).join('');
 else{const g={};w.forEach(x=>{const k=x[0]+'|'+x[1];(g[k]=g[k]||{x,n:0,r:0}).n++;if(x[2]&&x[3])g[k].r++});wl=Object.values(g).map(({x,n,r})=>`<div class="gun ${r===n?'rdy':'chg'}"><b>${x[1]||''}</b><span>${n} × ${esc(x[0])}<small>${r} ready</small></span><i>${r===n?'READY':r+'/'+n}</i></div>`).join('')}
 $('#cWpn').innerHTML=`<div class="sub">WEAPONS <em>${mt?esc(mt.n):FIREGROUP}</em></div>${wl}<div class="chips"><span class="${WSET.gimbals?'':'on'}">GIMBALS ${WSET.gimbals?'LOCKED':'FREE'}</span><span class="${WSET.stagger?'on':''}">${WSET.stagger?'STAGGERED':'NOT STAGGERED'}</span></div>`;
 const ms=S.missiles||0;
 $('#cCm').innerHTML=`<div class="sub">COUNTERMEASURES</div><div class="cmx"><span>DECOY</span><b>${CM.decoy??'—'}</b></div><div class="cmx"><span>NOISE</span><b class="${CM.noise!=null&&CM.noise<8?'lo':''}">${CM.noise??'—'}</b></div>`
  +(ms?`<div class="sub" style="margin-top:6px">MISSILES <em>loadout</em></div><div class="msl">${Array.from({length:Math.min(ms,12)},()=>'<i></i>').join('')}</div><div class="note2">${ms} on the racks · not tracked live</div>`:'');
 $('#cLog').innerHTML='<span class="sub">COMBAT LOG</span>'+(killRows(3).map(x=>`<span><b>${esc(x.killer||'Unknown')}</b> destroyed a ${esc(shipNameOf(x.cls))} · ${agoMin(x.t)}</span>`).join('')||'<span>No ships destroyed nearby</span>');
 cviewHead()}
function defense(){
 const bubble=((SH().caps||{}).sys||{}).faces!=='Quadrant';
 const SF=MODEL.shields||{},fc=SF.faces,bv=SF.bubble,f=bubble||!fc?(v=>({F:v,R:v,L:v,B:v}))(bv==null?null:bv):{F:fc[0],R:fc[1],B:fc[2],L:fc[3]};
 const hull=MODEL.hull;
 window.DG=drawDefense(f,hull,bubble);dlist(f,hull,bubble);cviewHead();cview();
 const A=window.AS||{}, zone={THR:['sEng'],WPN:['sWL','sWR'],PWR:['sCore'],COOL:['sCore'],RADR:['sNose'],QDRV:['sQt']};
 for(const [k,ids] of Object.entries(zone)){const v=A[k];if(v==='min'||v==='maj')ids.forEach(id=>{const e=$('#'+id);if(e)e.classList.add(v==='maj'?'zmaj':'zmin')})}
 const sg=$('#shipg');if(sg)sg.classList.toggle('hitflash',A.HIT==='maj');
 gunMarks();
}

/* ---- power: on/off only (5.0 has no pip levels) ---- */
/* T: VehicleFlightSettings Availability (enabled, active) */
const IFCS=()=>{const I=MODEL.ifcs||{},m={COUPLED:'ifcs_coupled',ESP:'ifcs_esp','G-SAFE':'ifcs_g_safe',CRUISE:'ifcs_cruise_mode',LIMITER:'ifcs_speed_limiter',PROX:'ifcs_proximity_assist',LANDING:'ifcs_proximity_assist_landing','GRAV COMP':'ifcs_gravity_compensation',STABILITY:'ifcs_stability'},o={};Object.entries(m).forEach(([n,k])=>{const v=I[k];o[n]=[v&&v.enabled?1:0,v&&v.active?1:0]});return o};
function ifcs(){const el=$('#ifcs');if(!el)return;el.innerHTML=Object.entries(IFCS()).map(([n,[en,ac]])=>`<span class="pc ${ac?'on':en?'en':''}" title="${n.toLowerCase()} · ${ac?'active':en?'on, not active':'off'}">${n}</span>`).join('')}
/* T: instruments. altitude >= 1e38 means no reference body */
let HDG=0;
function instruments(){const F=MODEL.flight,sp={alt:F.alt==null?'—':Math.round(F.alt).toLocaleString()+' m',agl:F.radarAlt==null?'—':Math.round(F.radarAlt).toLocaleString()+' m',atm:F.density==null?'—':F.density<0.01?'vacuum':F.density.toFixed(2),vsi:F.vsi==null?null:Math.round(F.vsi)};
 if($('#alt'))$('#alt').textContent=sp.alt;if($('#agl'))$('#agl').textContent=sp.agl;if($('#atm'))$('#atm').textContent=sp.atm;
 const v=$('#vsi');if(v){v.className='vsi'+(sp.vsi==null?' na':sp.vsi<0?' dn':'');v.textContent=sp.vsi==null?'VSI —':(sp.vsi<0?'▼ ':'▲ ')+Math.abs(sp.vsi)+' m/s'}
 const sp2=$('#spool');if(sp2)sp2.textContent=STATE.master==='nav'?' · spooled':'';
 const h=$('#hdg');if(h){const L={0:'N',90:'E',180:'S',270:'W'};let o='';for(let d=Math.ceil((HDG-60)/15)*15;d<=HDG+60;d+=15){const x=50+(d-HDG)/120*100,dd=((d%360)+360)%360;o+=`<i style="left:${x}%"></i>`+(Math.abs(x-50)>13&&x>6&&x<94?`<span class="${L[dd]!=null?'c':''}" style="left:${x}%">${L[dd]??String(dd).padStart(3,'0')}</span>`:'')}
  h.innerHTML=o+`<b>${String(Math.round(((HDG%360)+360)%360)).padStart(3,'0')}°</b>`}}
/* speedometer: sqrt scale so the SCM band gets room next to NAV */
const SPDS=()=>SPEEDS[shipK]||[200,500,1000];
function speedo(v){const el=$('#spdo');if(!el)return;const[scm,bst,max]=SPDS(),A0=-215,A1=35,cx=110,cy=96,r=80;
 const ang=x=>A0+(A1-A0)*Math.sqrt(Math.max(0,Math.min(1,x/max)));
 const pt=(a,rr)=>[cx+rr*Math.cos(a*Math.PI/180),cy+rr*Math.sin(a*Math.PI/180)];
 const arc=(a,b,rr)=>{const p=pt(a,rr),q=pt(b,rr);return `M${p[0].toFixed(1)} ${p[1].toFixed(1)} A${rr} ${rr} 0 ${b-a>180?1:0} 1 ${q[0].toFixed(1)} ${q[1].toFixed(1)}`};
 const col=v<=scm+.5?'#5bd1e6':v<=bst?'#ffb347':'#b58cff';
 let o=`<path d="${arc(A0,ang(scm),r)}" stroke="#12404d" stroke-width="12" fill="none"/><path d="${arc(ang(scm),ang(bst),r)}" stroke="#3a2e10" stroke-width="12" fill="none"/><path d="${arc(ang(bst),A1,r)}" stroke="#2a1d40" stroke-width="12" fill="none"/>`;
 if(v>0)o+=`<path d="${arc(A0,Math.max(A0+.5,ang(v)),r)}" stroke="${col}" stroke-width="12" fill="none" style="filter:drop-shadow(0 0 4px ${col}88)"/>`;
 const nd=pt(ang(v),r+9),nd2=pt(ang(v),r-9);const lg=document.querySelector('#spdl');if(lg)lg.innerHTML=`<span style="color:#5bd1e6">SCM ${scm.toLocaleString()}</span><span style="color:#ffb347">BOOST ${bst.toLocaleString()}</span><span style="color:#b58cff">NAV ${max.toLocaleString()}</span>`;o+=`<line x1="${nd[0].toFixed(1)}" y1="${nd[1].toFixed(1)}" x2="${nd2[0].toFixed(1)}" y2="${nd2[1].toFixed(1)}" stroke="#eafcff" stroke-width="2.5"/>`;
 for(const[x,l,c] of [[scm,'SCM','#5bd1e6'],...(bst<max?[[bst,'BOOST','#ffb347']]:[]),[max,bst<max?'NAV':'BOOST·NAV','#b58cff']]){const a=ang(x),t1=pt(a,r-7),t2=pt(a,r+7),lp=pt(a,r-22);
  o+=`<line x1="${t1[0].toFixed(1)}" y1="${t1[1].toFixed(1)}" x2="${t2[0].toFixed(1)}" y2="${t2[1].toFixed(1)}" stroke="${c}" stroke-width="2"/>`}
 el.innerHTML=o}
/* T: VehicleFlightState (is_landed, gear, vtol, transformation) */
function fstate(){const el=$('#fstate');if(!el)return;const c=CAPS(),landed=MODEL.flight.landed===true;
 const ch=(t,on,cls='')=>`<span class="fs ${on?'on':''} ${cls}">${t}</span>`;
 el.innerHTML=ch(landed?'LANDED':'FLYING',landed)+ch('GEAR '+(STATE.gear==null?'—':STATE.gear?'DOWN':'UP'),STATE.gear)+(c.vtol?ch('VTOL '+(STATE.vtol==null?'—':STATE.vtol?'ON':'OFF'),STATE.vtol):'')+(c.wings?ch('WINGS '+(STATE.wings==null?'—':STATE.wings?'OUT':'FOLDED'),STATE.wings):'')+(STATE.master?ch(STATE.master.toUpperCase(),true,'mm'):'')}
/* T: VehicleState name / manufacturer / type, lights_enabled, doors */
function shipInfo(){const S=SH(),id=$('#shipId'),st=$('#shipState');
 if(id)id.textContent=`${S.full} · spaceship`;
 if(st)st.innerHTML=[['LIGHTS',STATE.lights==null?'—':STATE.lights?'on':'off',STATE.lights],['DOORS',STATE.doors==null?'—':STATE.doors?'open':'closed',STATE.doors],['LOCK',STATE.locks==null?'—':STATE.locks?'locked':'unlocked',STATE.locks]].map(([n,v,on])=>`<span class="fs ${on?'on':''}">${n} ${v.toUpperCase()}</span>`).join('')}
/* T: ActorEnvironment (temp, radiation, breathable) + ActorVitals (gravity, pressure) + VehicleFlightInstruments.atmospheric_density */
function envData(){const e=MODEL.env,v=MODEL.vitals,F=MODEL.flight;return {air:e.breathable===false?'no air':'breathable',breath:e.breathable!==false,temp:e.temp==null?null:Math.round(e.temp),press:v.pressure==null?0:v.pressure,grav:v.gravity==null?0:v.gravity,rad:e.rad||0,dens:F.density==null?1:F.density}}
function envVerdict(E){const R=suitRating(true);
 if(!E.breath||E.dens<0.05)return ['bad',E.dens<0.05?'Vacuum outside · keep your helmet on':'No breathable air · keep your helmet on'];
 if(E.press<0.5)return ['bad','Low pressure · keep your helmet on'];
 if(E.temp!=null&&E.temp<R.tmin)return ['warn',`Too cold without a helmet · ${E.temp} °C, suit ${R.tmin} °C`];
 if(E.temp!=null&&E.temp>R.tmax)return ['warn',`Too hot without a helmet · ${E.temp} °C, suit ${R.tmax} °C`];
 if(E.rad>0.5)return ['warn','Radiation outside · keep your helmet on'];
 return ['ok','Safe without a helmet']}
function envCard(){const E=envData(),[cls,txt]=envVerdict(E),R=suitRating(true),v=$('#envVerdict'),g=$('#envGrid');
 if(v){v.className='envv '+cls;v.textContent=txt}
 const IJ=INJ(),TN={min:'minor',mod:'moderate',sev:'severe'},ij=Object.entries(IJ).filter(([k,l])=>TN[l]).map(([k,l])=>PART[k].toLowerCase().replace('left ','L ').replace('right ','R ')+' '+TN[l]),ie=$('#envInj');if(ie)ie.textContent=ij.length?ij.join(' · '):'no injuries';
 const V=VIT(),vv=$('#envVit');if(vv)vv.innerHTML=[['HP',V.hp],['O₂',V.o2],['FOOD',V.food],['WATER',V.water]].map(([n,x])=>`<span><span class="unit">${n}</span> <b style="color:${lvl(x)}">${x}%</b></span>`).join('');
 if(g)g.innerHTML=[['AIR',E.breath?'breathable':E.air,E.breath?'ok':'bad'],['TEMP',E.temp==null?'—':E.temp+' °C',E.temp==null?'':E.temp<R.tmin||E.temp>R.tmax?'warn':'ok'],['PRESSURE',E.press.toFixed(2)+' atm',E.press<0.5?'bad':'ok'],['GRAVITY',E.grav.toFixed(1)+' g',''],['RADIATION',E.rad.toFixed(1)+' REM/s',E.rad>0.5?'warn':''],['DENSITY',E.dens.toFixed(2),'']].map(([n,val,c])=>`<div class="ek"><span class="unit">${n}</span><b class="${c}">${val}</b></div>`).join('');
 return [cls,txt,E]}
function pips(){
 /* T: VehiclePowerState, 9 groups */
 const P=MODEL.power||{},on=k=>P[k]!==false,p=[['POWER PLANT',on('powerplants')],['ENGINES',on('engines')],['SHIELD GEN',on('shield_generators')],['EMITTERS',on('shield_emitters')],['WEAPONS',on('weapons')],['COOLERS',on('coolers')],['SENSORS',on('sensors')],['LIFE SUP',on('life_support')],['GRAVITY',on('gravity_generators')]];
 const A=window.AS||{}, W={'POWER PLANT':'PWR',ENGINES:'THR',WEAPONS:'WPN','SHIELD GEN':'SHLD',EMITTERS:'SHLD',COOLERS:'COOL',SENSORS:'RADR'};
 ifcs();instruments();fstate();shipInfo();envCard();hud();suitHud();speedo(+($('#spd')||{}).textContent||0);
 $('#pips').innerHTML=p.map(([n,on])=>{const w=on&&(A[W[n]]==='maj'||A[W[n]]==='min')?A[W[n]]:'';return `<span class="pc ${on?'on':''} ${w}">${n}</span>`}).join('');
}

/* ---- injury figure (Game.log: body part + tier) ---- */
const INJ=()=>Object.assign({h:'ok',t:'ok',la:'ok',ra:'ok',ll:'ok',rl:'ok'},(MODEL.log||{}).injuries||{});
/* front-facing armoured body, viewBox 0 0 120 230. Parts: h t ra la rl ll. Figure faces you: its right arm is on your left. */
const BODY={h:'M60 5C72 5 80 13 80 26C80 37 74 45 60 46C46 45 40 37 40 26C40 13 48 5 60 5Z',
 t:'M44 51Q60 47 76 51L87 57Q92 69 87 84L83 100Q81 111 83 121L60 125L37 121Q39 111 37 100L33 84Q28 69 33 57Z',
 ra:'M32 58Q23 61 21 73L17 104Q15 117 13 127Q12 135 17 136Q22 136 23 128L27 106L33 83Z',
 la:'M88 58Q97 61 99 73L103 104Q105 117 107 127Q108 135 103 136Q98 136 97 128L93 106L87 83Z',
 rl:'M38 123L59 127L57 168L55 205Q55 213 57 219L40 221Q39 213 41 205L39 168Z',
 ll:'M82 123L61 127L63 168L65 205Q65 213 63 219L80 221Q81 213 79 205L81 168Z'};
const BODYDECO=`<path d="M47 21Q60 16 73 21L71 30Q60 34 49 30Z" fill="rgba(91,209,230,.28)" stroke="none"/>
 <g fill="none" stroke="rgba(160,220,235,.35)" stroke-width="1"><path d="M60 52V110"/><path d="M44 66Q60 72 76 66"/><path d="M39 109H81"/><path d="M45 50Q60 56 75 50"/>
 <path d="M20 88L30 90"/><path d="M100 88L90 90"/><circle cx="48" cy="170" r="4"/><circle cx="72" cy="170" r="4"/><path d="M42 205H56"/><path d="M64 205H78"/></g>`;
function bodySvg(s,cls,{fill,stroke,sw=1.6,glow=false}){
 return `<svg viewBox="0 0 120 230" class="${cls}">`+Object.entries(BODY).map(([k,d])=>`<path d="${d}" fill="${fill(s[k])}" stroke="${stroke(s[k])}" stroke-width="${sw}" stroke-linejoin="round"${glow&&s[k]!=='ok'?` style="filter:drop-shadow(0 0 4px ${stroke(s[k])})"`:''}/>`).join('')+BODYDECO+'</svg>'}
function fig(){
 const s=INJ(), col={ok:'#1d5a3b',min:'#ffb347',mod:'#ff8a3d',sev:'#ff5468'}, st={ok:'#5be69a',min:'#ffb347',mod:'#ff8a3d',sev:'#ff5468'};
 const part=(k,d)=>`<path d="${d}" fill="${col[s[k]]}" stroke="${st[s[k]]}" stroke-width="1.2"/>`;
 const hurt=Object.entries(s).filter(([k,v])=>v!=='ok').map(([k,v])=>({la:'L arm',ra:'R arm',ll:'L leg',rl:'R leg',t:'Torso',h:'Head'}[k]+' · '+{min:'minor',mod:'moderate',sev:'severe'}[v]));
 const svg=bodySvg(s,'fig',{fill:v=>v==='ok'?'#0f2a33':col[v]+'88',stroke:v=>st[v],sw:2.2,glow:true});
 $$('[data-fig]').forEach(e=>e.innerHTML=svg+`<div class="figtxt">${hurt.length?hurt.join('<br>'):'No injuries'}</div>`);
}

/* ---- deck ---- */
/* ---- control deck: built per ship and seat from ship data (scunpacked) + the 5.0 action allowlist ---- */
/* keys show the game's state only. Live View never sends input to the game. */
let STATE={master:'scm',gear:false,vtol:false,wings:false,doors:false,locks:false,lights:false,coupled:false,power:true,nv:false};
const BTN={

 gear:{icon:'i-gear',l:'GEAR',kind:'tog',st:'gear',on:'down',off:'up'},
 wings:{icon:'i-wings',l:'WINGS',kind:'tog',st:'wings',on:'extended',off:'folded',need:c=>c.wings},
 vtol:{icon:'i-vtol',l:'VTOL',kind:'tog',st:'vtol',on:'on',off:'off',need:c=>c.vtol},

 doors:{icon:'i-doors',l:'DOORS',kind:'tog',st:'doors',on:'open',off:'closed'},
 locks:{icon:'i-lock',l:'LOCKS',kind:'tog',st:'locks',on:'locked',off:'unlocked'},
 lights:{icon:'i-lights',l:'LIGHTS',kind:'tog',st:'lights',on:'on',off:'off'},


 coupled:{icon:'i-coupled',l:'COUPLED',kind:'tog',st:'coupled',on:'on',off:'decoupled'},
 power:{icon:'i-power',l:'POWER',kind:'tog',st:'power',on:'on',off:'off'},





};
const OPS={};
let opSent=null, armed=null;
const CAPS=()=>SH().caps||{};
const seatEject=s=>{const c=CAPS(), seats=c.seats||[];
 if(s==='pilot')return seats.some(x=>/helm|pilot/i.test(x[1]||'')&&x[3]);
 const t=myTurretObj(); const m=t&&seats.find(x=>x[0]===t.id||(x[0]||'').replace(/^hardpoint_|seat_/g,'')===t.id.replace(/^hardpoint_/,''));
 return m?m[3]:seats.some(x=>/turret|gunner/i.test(x[1]||'')&&x[3])};
function opList(){const c=CAPS(), S=SH(), o=[];
 const guns=S.pilot.length>0, mis=(c.missiles||0)>0;
 if(c.mining)o.push('MINING'); if(c.salvage)o.push('SALVAGE');
 if(guns)o.push('GUNS'); if(mis&&o.length<2)o.push('MISSILES');
 o.push('SCAN'); return o.slice(0,3)}
function buildDeck(cells){
 if(seat==='gunner')return {list:[],info:'Turret aim and modes stay in game.'};
 const c=CAPS(), guards=[];
 const pri=['gear','wings','vtol','doors','locks','lights','coupled','power'].filter(id=>!BTN[id].need||BTN[id].need(c));
 const room=cells-1-guards.length;
 return {master:true,ops:[],list:[...pri.slice(0,room),...guards].map(id=>({id,...BTN[id]}))}}
let SD=null;const sdLeft=()=>0;const mmss=n=>Math.floor(n/60)+':'+String(n%60).padStart(2,'0');function sdStart(){}function sdCancel(){}function sdRender(){}
function btnHtml(b){let cls='btn cut',sub='';
  if(b.kind==='tog'){const on=STATE[b.st];if(on)cls+=' lit';sub=on==null?'—':on?b.on:b.off}
 else sub=b.s;
 return `<button class="${cls}" data-id="${b.id}">${ic(b.icon)}<span class="bl">${b.l}</span><small>${sub}</small></button>`}
function deckHtml(cells){const D=buildDeck(cells);let h='';
 if(D.master)h+=`<div class="btn cut split"><button class="seg ${STATE.master==='nav'?'on':''}" data-mm="nav">NAV<small>travel</small></button><button class="seg ${STATE.master==='scm'?'on':''}" data-mm="scm">SCM<small>combat</small></button></div>`;
 h+=D.list.map(btnHtml).join('');
 if(D.info)h+=`<div class="deckinfo" style="grid-column:1/-1">${D.info}</div>`;
 return h}
function bindDeck(root){}
function deckRender(){const d=$('#deck');if(!d)return;d.innerHTML=deckHtml(12);d.classList.toggle('gdeck',seat==='gunner');const nk=d.children.length;d.style.gridTemplateColumns=`repeat(${seat==='gunner'?Math.max(1,nk):Math.max(1,Math.ceil(nk/2))},1fr)`;bindDeck(d)}
function press(){}

/* ---- needs + feed ---- */
/* notifications: bell in the threat strip, recent list behind it, toasts for new ones */
let unread=0, bellOpen=false;
function bellHtml(){const n=needs.filter(x=>!x.done).length+unread;return `<button class="bell ${bellOpen?'open':''}" id="bellBtn" title="notifications">${ic('i-bell')}${n?`<i class="bb">${n>99?'99+':n}</i>`:''}</button>`}
/* SCREEN key: fullscreen where the browser allows it, and the screen stays on.
   Wake Lock only exists on HTTPS or localhost. The LAN link is plain HTTP, so a muted looping video holds the screen there. */
const KA_MP4='data:video/mp4;base64,AAAAIGZ0eXBpc29tAAACAGlzb21pc28yYXZjMW1wNDEAAAMybW9vdgAAAGxtdmhkAAAAAAAAAAAAAAAAAAAD6AAAB9AAAQAAAQAAAAAAAAAAAAAAAAEAAAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAgAAAl10cmFrAAAAXHRraGQAAAADAAAAAAAAAAAAAAABAAAAAAAAB9AAAAAAAAAAAAAAAAAAAAAAAAEAAAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAABAAAAAABAAAAAQAAAAAAAkZWR0cwAAABxlbHN0AAAAAAAAAAEAAAfQAAAAAAABAAAAAAHVbWRpYQAAACBtZGhkAAAAAAAAAAAAAAAAAABAAAAAgABVxAAAAAAALWhkbHIAAAAAAAAAAHZpZGUAAAAAAAAAAAAAAABWaWRlb0hhbmRsZXIAAAABgG1pbmYAAAAUdm1oZAAAAAEAAAAAAAAAAAAAACRkaW5mAAAAHGRyZWYAAAAAAAAAAQAAAAx1cmwgAAAAAQAAAUBzdGJsAAAAuHN0c2QAAAAAAAAAAQAAAKhhdmMxAAAAAAAAAAEAAAAAAAAAAAAAAAAAAAAAABAAEABIAAAASAAAAAAAAAABFUxhdmM2MS4zMy4xMDIgbGlieDI2NAAAAAAAAAAAAAAAGP//AAAALmF2Y0MBQsAe/+EAFmdCwB7ZHsBEAAADAAQAAAMAEDxYuSABAAVoy4PLIAAAABBwYXNwAAAAAQAAAAEAAAAUYnRydAAAAAAAAAqAAAAAAAAAABhzdHRzAAAAAAAAAAEAAAAEAAAgAAAAABRzdHNzAAAAAAAAAAEAAAABAAAAHHN0c2MAAAAAAAAAAQAAAAEAAAAEAAAAAQAAACRzdHN6AAAAAAAAAAAAAAAEAAACgwAAAAoAAAAKAAAACQAAABRzdGNvAAAAAAAAAAEAAANiAAAAYXVkdGEAAABZbWV0YQAAAAAAAAAhaGRscgAAAAAAAAAAbWRpcmFwcGwAAAAAAAAAAAAAAAAsaWxzdAAAACSpdG9vAAAAHGRhdGEAAAABAAAAAExhdmY2MS45LjEwNwAAAAhmcmVlAAACqG1kYXQAAAJwBgX//2zcRem95tlIt5Ys2CDZI+7veDI2NCAtIGNvcmUgMTY0IHIzMjA0IDM3MzY5N2IgLSBILjI2NC9NUEVHLTQgQVZDIGNvZGVjIC0gQ29weWxlZnQgMjAwMy0yMDI1IC0gaHR0cDovL3d3dy52aWRlb2xhbi5vcmcveDI2NC5odG1sIC0gb3B0aW9uczogY2FiYWM9MCByZWY9MyBkZWJsb2NrPTE6MDowIGFuYWx5c2U9MHgxOjB4MTExIG1lPWhleCBzdWJtZT03IHBzeT0xIHBzeV9yZD0xLjAwOjAuMDAgbWl4ZWRfcmVmPTEgbWVfcmFuZ2U9MTYgY2hyb21hX21lPTEgdHJlbGxpcz0xIDh4OGRjdD0wIGNxbT0wIGRlYWR6b25lPTIxLDExIGZhc3RfcHNraXA9MSBjaHJvbWFfcXBfb2Zmc2V0PS0yIHRocmVhZHM9MSBsb29rYWhlYWRfdGhyZWFkcz0xIHNsaWNlZF90aHJlYWRzPTAgbnI9MCBkZWNpbWF0ZT0xIGludGVybGFjZWQ9MCBibHVyYXlfY29tcGF0PTAgY29uc3RyYWluZWRfaW50cmE9MCBiZnJhbWVzPTAgd2VpZ2h0cD0wIGtleWludD0yNTAga2V5aW50X21pbj0yIHNjZW5lY3V0PTQwIGludHJhX3JlZnJlc2g9MCByY19sb29rYWhlYWQ9NDAgcmM9Y3JmIG1idHJlZT0xIGNyZj0yMy4wIHFjb21wPTAuNjAgcXBtaW49MCBxcG1heD02OSBxcHN0ZXA9NCBpcF9yYXRpbz0xLjQwIGFxPTE6MS4wMACAAAAAC2WIhAU8mKAAP7+AAAAABkGaOAp6gAAAAAZBmlQCnqAAAAAFQZpgEvU=',KA_WEBM='data:video/webm;base64,GkXfo59ChoEBQveBAULygQRC84EIQoKEd2VibUKHgQJChYECGFOAZwEAAAAAAAIrEU2bdLpNu4tTq4QVSalmU6yBoU27i1OrhBZUrmtTrIHWTbuMU6uEElTDZ1OsggEjTbuMU6uEHFO7a1OsggIV7AEAAAAAAABZAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAVSalmsCrXsYMPQkBNgIxMYXZmNjEuOS4xMDdXQYxMYXZmNjEuOS4xMDdEiYhAn0AAAAAAABZUrmvIrgEAAAAAAAA/14EBc8WI0ljdNRy+CE6cgQAitZyDdW5kiIEAhoVWX1ZQOIOBASPjg4QdzWUA4JCwgRC6gRCagQJVsIRVuYEBElTDZ/tzc59jwIBnyJlFo4dFTkNPREVSRIeMTGF2ZjYxLjkuMTA3c3PWY8CLY8WI0ljdNRy+CE5nyKFFo4dFTkNPREVSRIeUTGF2YzYxLjMzLjEwMiBsaWJ2cHhnyKFFo4hEVVJBVElPTkSHkzAwOjAwOjAyLjAwMDAwMDAwMAAfQ7Z17eeBAKOjgQAAgBACAJ0BKhAAEAAARwiFhYiFhIgCAgAMDWAA/v+rUICjlYEB9ACxAQABEBAAGAAYWC/0AAgAAKOVgQPoALEBAAEQEAAYABhYL/QACAAAo5WBBdwAsQEAARAQABgAGFgv9AAIAAAcU7trkbuPs4EAt4r3gQHxggGj8IED';
const SCR={on:false,lock:null,vid:null,how:''};
const STANDALONE=navigator.standalone===true||matchMedia('(display-mode: standalone),(display-mode: fullscreen)').matches;
function scrHtml(){const t=!SCR.on?'Fullscreen, screen stays on':SCR.how==='blocked'?'Screen stays on: blocked by the browser. Tap to retry.'
  :fsCan()&&!fsEl()?`Screen stays on (${SCR.how||'starting'}). Tap for fullscreen.`:`Screen stays on (${SCR.how||'starting'}). Tap to release.`;
 return `<button class="bell scr ${SCR.on?'on':''}" id="scrBtn" type="button" title="${t}" aria-pressed="${SCR.on}"><i class="led"></i>${ic('i-screen')}</button>`}
function scrSync(){const b=$('#scrBtn');if(b)b.outerHTML=scrHtml()}
function fsEl(){return document.fullscreenElement||document.webkitFullscreenElement}
function fsCan(){const e=document.documentElement;return !!(e.requestFullscreen||e.webkitRequestFullscreen)}
function fsGo(){const e=document.documentElement,f=e.requestFullscreen||e.webkitRequestFullscreen;if(!f||fsEl())return;try{const r=f.call(e,{navigationUI:'hide'});if(r&&r.catch)r.catch(()=>{})}catch(x){}}
function fsExit(){const f=document.exitFullscreen||document.webkitExitFullscreen;if(!fsEl()||!f)return;try{const r=f.call(document);if(r&&r.catch)r.catch(()=>{})}catch(x){}}
function scrVideo(){if(!SCR.vid){const v=document.createElement('video');v.muted=true;v.loop=true;v.setAttribute('muted','');v.setAttribute('playsinline','');v.setAttribute('aria-hidden','true');
  v.style.cssText='position:fixed;inset:0;width:100vw;height:100vh;object-fit:fill;opacity:.01;pointer-events:none;z-index:-1;background:#000';
  v.innerHTML=`<source src="${KA_WEBM}" type="video/webm"><source src="${KA_MP4}" type="video/mp4">`;document.body.appendChild(v);SCR.vid=v}
 return SCR.vid.play().then(()=>{SCR.how='video'}).catch(()=>{SCR.how='blocked'})}
function scrHold(){
 if('wakeLock' in navigator&&window.isSecureContext)return navigator.wakeLock.request('screen').then(l=>{SCR.lock=l;SCR.how='wake lock';l.addEventListener('release',()=>{SCR.lock=null})}).catch(scrVideo);
 return scrVideo()}
function scrRelease(){if(SCR.lock){SCR.lock.release().catch(()=>{});SCR.lock=null}if(SCR.vid)SCR.vid.pause();SCR.how=''}
function scrPref(on){try{localStorage.setItem('csr.awake',on?'1':'0')}catch(x){}}
function scrToggle(){
 // on but not fullscreen yet: the tap goes fullscreen and keeps the screen on
 if(SCR.on&&fsCan()&&!fsEl()){fsGo();if(SCR.how!=='wake lock'&&SCR.how!=='video')scrHold().then(scrSync);scrSync();return}
 if(SCR.on){SCR.on=false;scrPref(false);scrRelease();fsExit();scrSync();return}
 SCR.on=true;scrPref(true);fsGo();scrSync();scrHold().then(scrSync);   // both start inside the tap: browsers refuse them later
 if(!fsCan()&&!STANDALONE){let seen=false;try{seen=localStorage.getItem('csr.a2hs')==='1';localStorage.setItem('csr.a2hs','1')}catch(x){}
  if(!seen)toast('i-screen','tip','For fullscreen: Share › Add to Home Screen')}}
document.addEventListener('click',e=>{if(e.target.closest('#scrBtn')){e.stopPropagation();scrToggle()}},true);
// the wake lock drops and the video pauses while the tab is hidden: pick it back up on return
document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible'&&SCR.on&&!SCR.lock)scrHold().then(scrSync)});
['fullscreenchange','webkitfullscreenchange'].forEach(t=>document.addEventListener(t,scrSync));
// the slate is meant to stay up: keep the screen on from the start unless it was switched off
(function(){let off=false;try{off=localStorage.getItem('csr.awake')==='0'}catch(x){}
 if(off)return;SCR.on=true;scrHold().then(scrSync);
 // autoplay can be refused (battery saver, data saver): the first touch anywhere retries
 const retry=()=>{if(SCR.on&&SCR.how==='blocked')scrHold().then(scrSync)};
 document.addEventListener('pointerdown',retry,{capture:true,once:true})})();
function threatBell(){const b=document.querySelector('#bellBtn');if(b)b.outerHTML=bellHtml()}
function bellRender(){let p=document.querySelector('#bellPanel');if(!bellOpen){if(p)p.remove();return}
 if(!p){p=document.createElement('div');p.id='bellPanel';p.className='cut';app.appendChild(p)}
 const open=needs.filter(n=>!n.done);
 p.innerHTML=`<div class="ph">RECENT</div><div class="bellfeed">${foldFeed(feed,'all').slice(0,8).map(feedRow).join('')||'<div class="unit">No notices yet</div>'}</div>
  <button class="mapbtn bellall" id="bellAll">ALL NOTIFICATIONS ON FEED ▸</button>`;
 p.querySelector('#bellAll').onclick=e=>{e.stopPropagation();closeBell();document.querySelector('#bezel .osb[data-p="feed"]').click()};
 p.querySelectorAll('.need').forEach(el=>el.onclick=e=>{e.stopPropagation();const n=needs.find(x=>x.id==el.dataset.n);n.done='dismissed';needs_();bellRender()})}
function toggleBell(){bellOpen=!bellOpen;if(bellOpen){unread=0;const t=document.querySelector('#toasts');if(t)t.innerHTML=''}threatBell();bellRender()}
function closeBell(){if(!bellOpen)return;bellOpen=false;threatBell();bellRender()}
app.addEventListener('click',e=>{if(e.target.closest('#bellBtn')){e.stopPropagation();toggleBell();return}if(bellOpen&&!e.target.closest('#bellPanel'))closeBell()});
/* ticker: answer-in-game first, then the newest notifications, one at a time; a new one jumps to the front */
let TK={i:0,flash:null,until:0};
function tickerItems(){const L=needs.filter(n=>!n.done).map(n=>({icon:NTYPE[n.type][0],grp:'',txt:NTYPE[n.type][1]+(n.what?' · '+n.what:'')+' · '+n.who,ans:true,t:n.t}));
 return L.concat(foldFeed(feed,'all').slice(0,6).map(r=>({icon:r.e[1],grp:r.e[2],txt:r.aw&&r.n>1?`Awarded ${r.sum.toLocaleString()} aUEC`:r.e[3],t:r.e[0]})))}
function placeTicker(){let k=document.querySelector('#ticker');if(phone){if(k)k.remove();return}
 if(!k){k=document.createElement('div');k.id='ticker';k.title='notifications';app.appendChild(k);k.onclick=e=>{e.stopPropagation();toggleBell()}}
 const th=document.querySelector('#threat');if(!th||!th.offsetWidth){k.style.display='none';return}
 const ck=th.querySelector('.clock'),bl=th.querySelector('#scrBtn')||th.querySelector('#bellBtn');
 const keep=[...th.children].filter(c=>c.offsetParent&&!c.classList.contains('long')&&!c.classList.contains('clock')&&c.id!=='bellBtn'&&c.id!=='scrBtn'&&!c.classList.contains('sep'));
 const anc=ck&&ck.offsetParent?ck:bl;if(!anc){k.style.display='none';return}
 const left=(keep.length?Math.max(...keep.map(c=>c.offsetLeft+c.offsetWidth)):th.offsetLeft)+14,right=anc.offsetLeft-12,w=right-left;
 if(w<180){k.style.display='none';return}
 Object.assign(k.style,{display:'',left:left+'px',width:w+'px',top:(th.offsetTop+3)+'px',height:(th.offsetHeight-6)+'px'});tickerRender(false)}
function tickerRender(anim){const k=document.querySelector('#ticker');if(!k||k.style.display==='none')return;
 const it=TK.flash&&Date.now()<TK.until?TK.flash:(()=>{const L=tickerItems();if(!L.length)return null;TK.i%=L.length;return L[TK.i]})();
 if(!it){k.innerHTML='';return}const G=FG[it.grp]||FG.msn,col=it.ans?'#ffb347':G[1],lab=it.ans?'ANSWER IN GAME':G[0];
 k.innerHTML=`<div class="tki ${anim?'in':''} ${it===TK.flash?'new':''}" style="--tc:${col}">${ic(it.icon,'ic sm')}<span class="tl">${lab}</span><span class="tt">${esc(it.txt)}</span><span class="ta">${it.t||'now'}</span></div>`}
function tickerFlash(it){it.t='now';TK.flash=it;TK.until=Date.now()+(it.ans?9000:5000);tickerRender(true)}
setInterval(()=>{if(TK.flash&&Date.now()<TK.until)return;TK.flash=null;TK.i++;tickerRender(true)},4500);
function placeToasts(t){t=t||document.querySelector('#toasts');if(!t)return;t.classList.remove('instrip');t.style.cssText='';if(phone)return;
 const th=document.querySelector('#threat');if(!th)return;const ck=th.querySelector('.clock'),bl=th.querySelector('#scrBtn')||th.querySelector('#bellBtn');
 const keep=[...th.children].filter(c=>c.offsetParent&&!c.classList.contains('long')&&!c.classList.contains('clock')&&c.id!=='bellBtn'&&c.id!=='scrBtn'&&!c.classList.contains('sep'));
 const left=keep.length?Math.max(...keep.map(c=>c.offsetLeft+c.offsetWidth))+12:th.offsetLeft+12, right=(ck&&ck.offsetParent?ck:bl).offsetLeft-10, w=right-left;
 if(w>=220){t.classList.add('instrip');t.style.top=th.offsetTop+'px';t.style.height=th.offsetHeight+'px';t.style.left=(right-Math.min(470,w))+'px';t.style.width=Math.min(470,w)+'px'}}
function toast(icon,grp,txt,ans){if(!phone){tickerFlash({icon,grp,txt,ans});return}if(bellOpen)return;let t=document.querySelector('#toasts');if(!t){t=document.createElement('div');t.id='toasts';app.appendChild(t)}
 const G=FG[grp]||FG.msn, el=document.createElement('div');el.className='toast cut'+(ans?' ans':'');if(!ans)el.style.setProperty('--tc',G[1]);
 el.innerHTML=`${ic(icon,'ic sm')}<div><div class="tl">${ans?'ANSWER IN GAME':G[0]}</div><div>${esc(txt)}</div></div>`;
 el.onclick=e=>{e.stopPropagation();el.remove();toggleBell()};
 placeToasts(t);if(t.classList.contains('instrip'))t.innerHTML='';
 t.prepend(el);while(t.children.length>3)t.lastChild.remove();
 setTimeout(()=>{el.classList.add('out');setTimeout(()=>el.remove(),300)},ans?12000:5000)}
function needHtml(n){const T=NTYPE[n.type];return `<div class="need cut ${n.done?'done':''}" data-n="${n.id}" title="closes when the log shows: ${T[3]}">${ic(T[0],'ic sm')}<div><div>${T[1]}${n.what?` <span class="nwhat">· ${esc(n.what)}</span>`:''}</div><div class="who">${n.done?'✓ '+n.done:esc(n.who)}</div></div><span class="t">${n.done?'':n.t}</span></div>`}
function needs_(){
 const open=needs.filter(n=>!n.done).length, list=[...needs].sort((a,b)=>(a.done?1:0)-(b.done?1:0));
 $('#needRail').innerHTML=list.filter(n=>!n.done).map(needHtml).join('')||'<div class="unit">Nothing waiting.</div>';
 $('#needFull').innerHTML=list.map(needHtml).join('')+`<div class="unit" style="margin-top:6px">Accept or decline with your in-game keys. Each card closes when the game log shows the answer. Tap to dismiss.</div>`;
 $('#bdg').textContent=open;$('#bdg').style.display=open?'':'none';$('#needcount').textContent=open||'';threatBell();
 $$('.need').forEach(el=>el.onclick=()=>{const n=needs.find(x=>x.id==el.dataset.n);n.done=n.done?'':'dismissed';needs_()});
}
/* fold noise: merge back-to-back awards, collapse runs of zone changes, hide comms channels outside SHIP */
function foldFeed(list,f){const out=[];
 for(const e of list){
  if(f==='all'&&e[6]==='chan')continue;
  const last=out[out.length-1];
  if(last&&e[6]==='aw'&&last.aw&&last.e[0]===e[0]){last.n++;last.sum+=+(e[3].match(/[\d,]+/)||['0'])[0].replace(/,/g,'');continue}
  if(f==='all'&&e[2]==='zone'&&last&&last.zone){last.items.push(e[3]);continue}
  out.push(e[6]==='aw'?{e,aw:true,n:1,sum:+(e[3].match(/[\d,]+/)||['0'])[0].replace(/,/g,'')}:f==='all'&&e[2]==='zone'?{e,zone:true,items:[e[3]]}:{e})}
 return out}
function feedRow(r){const e=r.e, g=FG[e[2]]||FG.msn;
 let txt=esc(e[3]), sub=e[5];
 if(r.aw&&r.n>1){txt=`Awarded ${r.sum.toLocaleString()} aUEC`;sub=`${r.n} payments`}
 if(r.zone&&r.items.length>1){txt=esc(r.items[0]);sub=`+${r.items.length-1} more zone change${r.items.length>2?'s':''}`}
 return `<div class="ev g-${e[2]} ${e[6]==='bp'?'bp':''} ${e[4]?'new':''}"><span class="t">${e[0]}</span>${ic(e[1],'ic sm')}<span class="evt"><span>${txt}</span>${sub?`<span class="evs">${esc(sub)}</span>`:''}</span></div>`}
function mapFit(){const mw=document.querySelector('.mapp .mapwrap');if(!mw||!mw.clientWidth)return;const n=mw.clientWidth<540;mw.classList.toggle('narrow',n);if(!n)mw.classList.remove('open');const h=mw.querySelector('.dhandle span');if(h)h.textContent=mw.classList.contains('open')?'▸':'◂'}
document.addEventListener('click',e=>{const b=e.target.closest('.dhandle');if(!b)return;const mw=b.closest('.mapwrap');mw.classList.toggle('open');mapFit()});
function trimDist(){mapFit();document.querySelectorAll('.mapwrap .dist').forEach(d=>{const rows=[...d.children];rows.forEach(r=>r.style.display='');if(getComputedStyle(d).position!=='absolute'||!d.clientHeight)return;
 const body=rows.filter(r=>!r.classList.contains('dzone'));
 for(let i=body.length-1;i>=0&&d.scrollHeight>d.clientHeight+1;i--){const r=body[i];r.style.display='none';const prev=body[i-1];if(prev&&prev.classList.contains('dh')){const next=body.slice(i+1).find(x=>x.style.display!=='none');if(!next||next.classList.contains('dh'))prev.style.display='none'}}})}
function trimRail(){const fb=document.querySelector('.rail .feedbox');if(!fb)return;const b=fb.getBoundingClientRect().bottom-4;if(!b)return;let cut=false;fb.querySelectorAll('#feedRail .ev').forEach(e=>{e.style.display='';if(cut||e.getBoundingClientRect().bottom>b){e.style.display='none';cut=true}})}
function feed_(){
 $('#feedRail').innerHTML=foldFeed(feed,'all').slice(0,16).map(feedRow).join('')||'<div class="unit">No notices yet</div>';trimRail();
 const list=feed.filter(e=>filter==='all'||e[2]===filter);
 $('#feedFull').innerHTML=foldFeed(list,filter).map(feedRow).join('')||'<div class="unit">No notices yet</div>';
 const cnt=g=>feed.filter(e=>e[2]===g&&!(e[6]==='chan')).length;
 $('#filters').innerHTML=[['all','ALL',feed.filter(e=>e[6]!=='chan').length],...Object.entries(FG).map(([k,v])=>[k,v[0],cnt(k)])].map(([k,l,n])=>`<button class="${filter===k?'on':''}" data-f="${k}" style="${k!=='all'?'--fc:'+FG[k][1]:''}">${l}<i>${n}</i></button>`).join('');
 $$('#filters button').forEach(b=>b.onclick=()=>{filter=b.dataset.f;feed_()});
 feed.forEach(e=>e[4]=false);
}
function addFeed(icon,cat,txt){const d=new Date(),g=OLDCAT[cat]||cat;feed.unshift([d.toTimeString().slice(0,5),icon,g,txt,true,'','']);feed=feed.slice(0,60);feed_();
 if(!document.querySelector('#page-feed.on')){unread++;toast(icon,g,txt)}threatBell();if(bellOpen)bellRender()}



/* ---- pages ---- */
$$('#bezel .osb').forEach(b=>b.onclick=()=>{
 $$('#bezel .osb').forEach(x=>x.classList.toggle('on',x===b));
 $$('.page').forEach(p=>p.classList.toggle('on',p.id==='page-'+b.dataset.p));
 app.classList.toggle('feedpage',b.dataset.p==='feed');if(b.dataset.p==='feed'){unread=0;threatBell()}closeBell();
 trimRail();trimDist();fitThreat();placeTicker();
});

$$('[data-go]').forEach(el=>el.onclick=()=>document.querySelector(`#bezel .osb[data-p="${el.dataset.go}"]`).click());
/* ---- layout ---- */


const TMIN=[1180,820];
function layout(){
 phone=Math.min(innerWidth,innerHeight)<600; tablet=false;
 app.classList.toggle('phone',phone);
 app.classList.remove('tablet');
 document.body.classList.toggle('tabletmode',phone);
 const port=innerHeight>innerWidth;
 app.classList.toggle('portrait',port&&!phone);
 ['inset','width','height','transform','transformOrigin'].forEach(k=>app.style[k]='');
 if(phone){delete app.dataset.ds;app.style.setProperty('--s','1');app.style.setProperty('--pw',innerWidth+'px');app.style.setProperty('--ph',innerHeight+'px');app.style.marginTop='0';
  app.classList.toggle('phshort',innerHeight/innerWidth<1.9);app.classList.toggle('phnarrow',innerWidth<=360);app.classList.toggle('phtall',innerHeight/innerWidth>=2.15)}
 else{delete app.dataset.ds;app.style.removeProperty('--s');app.style.marginTop='';
  const ds=Math.min(1,innerWidth/(port?TMIN[1]:TMIN[0]),innerHeight/(port?TMIN[0]:TMIN[1]));
  if(ds<1){app.dataset.ds=ds.toFixed(3);Object.assign(app.style,{inset:'0 auto auto 0',width:Math.round(innerWidth/ds)+'px',height:Math.round(innerHeight/ds)+'px',transform:`scale(${ds})`,transformOrigin:'0 0'})}}
 if(typeof phoneRender==='function')phoneRender();
 if(typeof trimRail==='function')trimRail();
 if(typeof trimDist==='function')trimDist();
 if(typeof fitThreat==='function')fitThreat();
 const tt=document.querySelector('#toasts');if(tt)tt.innerHTML='';
 if(typeof placeTicker==='function')placeTicker();
}
/* ---- loadout (Game.log AttachmentReceived per port) ---- */
/* ---- gear: slots come from the pieces worn (scunpacked fps-items ports), ratings from the same data ---- */
const GEARCAT={"cds_combat_light_legs_03_03_xenothreat":["FBL-8a Legs Justified","legs","L",20,-35,65,26000,2500,{"med":2,"util":2,"oxy":2,"side":1},[]],"qrt_combat_medium_backpack_01_01_15":["Testudo Backpack Earthshake","backpack","M",null,null,null,null,105000,{"util":1},[["Secondary",4],["Primary",5]],"Medium & Heavy",2],"qrt_utility_heavy_backpack_02_01_01":["Palatino Backpack Mark I","backpack","H",null,null,null,null,180000,{"util":1},[["Secondary",4],["Primary",5]],"Heavy",3],"clda_env_armor_heavy_helmet_01_01_expo":["Novikov \"Expo\" Helmet","helmet","H",25,-225,75,33600,0,{"radar":1},[]],"qrt_utility_heavy_helmet_02_01_01":["Palatino Helmet Mark I","helmet","H",40,-70,100,26800,0,{"radar":1},[]],"cds_combat_light_core_03_03_xenothreat":["FBL-8a Core Justified","core","L",20,-32,62,26000,4000,{"mag":4,"gren":2,"bp":1,"util":1},[["Primary",5]]],"qrt_combat_medium_legs_01_01_15":["Testudo Legs Earthshake","legs","M",30,-50,80,26400,4000,{"med":2,"util":2,"oxy":2,"side":1},[]],"qrt_utility_heavy_arms_02_01_01":["Palatino Arms Mark I","arms","H",40,-70,100,26800,0,{},[]],"qrt_specialist_heavy_helmet_01_01_01":["Antium Helmet","helmet","H",40,-85,115,26800,0,{"radar":1},[]],"cds_combat_light_backpack_01_02_10":["Geist Backpack Whiteout","backpack","L",null,null,null,null,54000,{"util":1},[["Secondary",4],["Primary",5]],"All",1],"rsi_odyssey_undersuit_01_01_12":["Odyssey II Undersuit Exec","undersuit","",5,-30,60,15200,0,{"mag":2,"med":1,"util":1,"oxy":1,"side":1},[["Primary",5]]],"qrt_combat_medium_arms_01_01_15":["Testudo Arms Earthshake","arms","M",30,-50,80,26400,0,{},[]],"cds_combat_light_helmet_03_03_xenothreat":["FBL-8a Helmet Justified","helmet","L",20,-35,65,26000,0,{"radar":1},[]],"qrt_utility_heavy_legs_02_01_01":["Palatino Legs Mark I","legs","H",40,-70,100,26800,8000,{"med":2,"util":2,"oxy":2,"side":1},[]],"qrt_combat_medium_helmet_01_01_15":["Testudo Helmet Earthshake","helmet","M",30,-50,80,26400,0,{"radar":1},[]],"clda_env_heavy_backpack_01_01_01":["Novikov Backpack","backpack","H",null,null,null,null,180000,{"util":1},[["Secondary",4],["Primary",5]],"Heavy",3],"qrt_utility_heavy_core_01_01_01":["Palatino Core","core","H",40,-70,100,26800,8000,{"mag":8,"gren":4,"bp":3,"util":1},[["Secondary",4],["Primary",5]]],"clda_env_armor_heavy_suit_01_01_01":["Novikov Exploration Suit","undersuit","H",25,-225,75,33600,22000,{"mag":8,"gren":4,"bp":3,"util":3,"med":2,"oxy":2,"side":1},[["Secondary",4],["Primary",5]]],"rsi_odyssey_undersuit_helmet_01_01_02":["Odyssey II Racing Helmet Red","helmet","",5,-30,60,15200,0,{"radar":1},[]],"qrt_combat_medium_core_01_01_15":["Testudo Core Earthshake","core","M",30,-50,80,26400,8000,{"mag":6,"gren":3,"bp":2,"util":1},[["Secondary",4],["Primary",5]]],"qrt_specialist_heavy_core_01_01_01":["Antium Core","core","H",40,-95,120,26800,10500,{"mag":8,"gren":4,"bp":3,"util":1},[["Secondary",4],["Primary",5]]],"cds_combat_light_arms_03_03_xenothreat":["FBL-8a Arms Justified","arms","L",20,-35,65,26000,0,{},[]]};
function itemName(c){const N=MODEL.names||{};return N[String(c).toLowerCase()]||String(c)}
function LIVESET(){const lo=(MODEL.log||{}).loadout||{},g=p=>{const c=lo[p];return c&&c!=='Default'?c:null};
 const w={};Object.keys(lo).filter(k=>/^wep_stocked_\d+$/.test(k)).sort().forEach((k,i)=>{const c=g(k);if(c&&!w[i?'Secondary':'Primary'])w[i?'Secondary':'Primary']=[itemName(c),'']});
 if(g('wep_sidearm'))w.Sidearm=[itemName(g('wep_sidearm')),''];
 const pcs=['Armor_Helmet','Armor_Torso','Armor_Arms','Armor_Legs'].map(g).filter(Boolean),br=pcs.map(c=>itemName(c).split(' ')[0]);
 return {label:pcs.length?(br.every(b=>b===br[0])?br[0]+' set':'mixed armor'):'',helmet:g('Armor_Helmet'),core:g('Armor_Torso'),arms:g('Armor_Arms'),legs:g('Armor_Legs'),
  undersuit:g('Armor_Undersuit'),backpack:g('backpack'),w,hand:null,mag:null,gren:null,med:null,oxy:null,util:null}}
let outT=null;
const OUTSIDE=[[21,'']];
const G=k=>k?(GEARCAT[String(k).toLowerCase()]||[itemName(k),null,null,null,null,null,null,null,{},[]]):null, CLS={L:'LIGHT',M:'MEDIUM',H:'HEAVY'};
function gearSlots(){const L=LIVESET(), core=G(L.core), legs=G(L.legs), us=G(L.undersuit), bp=G(L.backpack);
 const cp=core?core[8]:(us?us[8]:{}), cw=core?core[9]:(us?us[9]:[]), lp=legs?legs[8]:(us?us[8]:{});
 const weap=[...cw].sort((x,y)=>(x[0]==='Primary'?0:1)-(y[0]==='Primary'?0:1)).map(w=>[w[0]||'Weapon',w[1]]).concat(bp?bp[9].map((w,i)=>['Backpack '+(i+1),w[1]]):[]).concat(lp.side?[['Sidearm',1]]:[]);
 const util=(core?cp.util||0:0)+(legs?lp.util||0:0)+(!core&&!legs&&us?us[8].util||0:0);
 return {weap,mag:cp.mag||0,gren:cp.gren||0,med:lp.med||0,oxy:lp.oxy||0,util,bpMax:cp.bp||0,bp,core,legs,us}}
function suitRating(noHelmet){const L=LIVESET(), us=G(L.undersuit);
 // each body area is rated by its outer layer: armor piece if worn, else the undersuit
 const areas=[['head',(noHelmet?null:G(L.helmet))||us,(L.helmet&&!noHelmet)?'helmet':'undersuit'],['torso',G(L.core)||us,L.core?'core':'undersuit'],['arms',G(L.arms)||us,L.arms?'arms':'undersuit'],['legs',G(L.legs)||us,L.legs?'legs':'undersuit']].filter(a=>a[1]);
 const withT=areas.filter(a=>a[1][4]!=null&&a[1][5]!=null);
 const tmin=withT.length?Math.max(...withT.map(a=>a[1][4])):null, tmax=withT.length?Math.min(...withT.map(a=>a[1][5])):null;
 const coldA=withT.find(a=>a[1][4]===tmin), hotA=withT.find(a=>a[1][5]===tmax);
 const rads=areas.map(a=>a[1][6]).filter(v=>v!=null),rad=rads.length?Math.min(...rads):null;
 const pcs=['helmet','core','arms','legs','undersuit'].map(k=>G(L[k])).filter(Boolean);
 const carry=pcs.some(x=>x[7]!=null)?pcs.reduce((a,x)=>a+(x[7]||0),0):null, bp=G(L.backpack);
 return {tmin,tmax,coldP:coldA&&coldA[0]+' ('+coldA[2]+')',hotP:hotA&&hotA[0]+' ('+hotA[2]+')',rad,carry,bpCap:bp?bp[7]:0}}
const fmtU=n=>n>=1000?Math.round(n/1000)+'K':String(n);
function suitTempState(){if(seat==='eva')return {ok:true,txt:'in space · no reading'};const R=suitRating();if(outT==null)return {ok:true,txt:'—'};if(R.tmin==null)return {ok:true,txt:outT+' °C'};
 if(outT<R.tmin)return {ok:false,txt:`${outT} °C · suit min ${R.tmin}`,cold:true};
 if(outT>R.tmax)return {ok:false,txt:`${outT} °C · suit max ${R.tmax}`,hot:true};
 return {ok:true,txt:`${outT} °C · in range`}}

function loadout(){const L=LIVESET(), S=gearSlots(), R=suitRating(), T=suitTempState();
 const brand=n=>(n||'').split(' ')[0];
 const pieces=['helmet','core','arms','legs'].map(k=>G(L[k])).filter(Boolean);
 const same=pieces.length&&pieces.every(x=>brand(x[0])===brand(pieces[0][0]));
 const armorTxt=pieces.length?(same?`${brand(pieces[0][0])} set · ${CLS[(G(L.core)||pieces[0])[2]]?.toLowerCase()||''}`:'mixed armor'):(G(L.undersuit)||['—'])[0];
 const wl=Object.entries(L.w);
 const mini=`<div class="lrow"><svg class="ic sm"><use href="#i-shield"/></svg><span>${esc(armorTxt)}</span><span class="unit">${T.ok?'':'<span style="color:#ff8a94">temp!</span>'}</span></div>`+
  wl.slice(0,2).map(([slot,w])=>`<div class="lrow"><svg class="ic sm"><use href="#i-weapon"/></svg><span>${esc(w[0])}</span><span class="unit">${slot.toLowerCase()}</span></div>`).join('')+
  `<div class="lrow"><svg class="ic sm"><use href="#s-pack"/></svg><span>${L.backpack?esc(G(L.backpack)[0]):'No backpack'}</span><span class="unit">backpack</span></div>`;
 const miniEva=`<div class="lrow"><svg class="ic sm"><use href="#i-shield"/></svg><span class="lone">${esc([armorTxt,...wl.map(x=>x[1][0].split(' ')[0])].join(' · '))}</span></div>`;
 const sec=(t,rows)=>`<div class="lsec"><div class="lh">${t}</div>${rows}</div>`;
 const full=sec('ARMOR',['helmet','core','arms','legs','undersuit'].map(k=>{const x=G(L[k]);return `<div class="lrow"><span class="lk">${k}</span><span>${x?esc(x[0]):'none'}</span></div>`}).join(''))+
  sec('WEAPONS',S.weap.map(([slot])=>{const w=L.w[slot];return `<div class="lrow"><span class="lk">${slot}</span><span>${w?esc(w[0]):'empty'}</span></div>`}).join(''));
 $$('[data-load="mini"]').forEach(e=>e.innerHTML=seat==='eva'?miniEva:mini);$$('[data-load="full"]').forEach(e=>e.innerHTML=full);
 doll();
}
/* ---- paper doll: slots from the worn pieces, injuries on the body ---- */
function doll(){
 if(!$('#slotsL'))return;
 const L=LIVESET(), S=gearSlots(), R=suitRating(), T=suitTempState();
 const s=INJ(), word={ok:'',min:'minor',mod:'moderate',sev:'severe'}, col={ok:'#5be69a',min:'#ffb347',mod:'#ff8a3d',sev:'#ff5468'};
 const inj=(...ks)=>{const w=ks.map(k=>s[k]).filter(v=>v!=='ok');if(!w.length)return '';const worst=w.includes('sev')?'sev':w.includes('mod')?'mod':'min';return `<span class="ichip" style="border-color:${col[worst]};color:${col[worst]}">${word[worst]}</span>`};
 const tile=(icon,slot,name,sub,extra='',empty=false)=>`<div class="slot cut ${empty?'empty':''}"><div class="sic">${ic(icon)}</div><div class="stx"><div class="sl">${slot}${extra}</div><div class="sn">${name}</div>${sub?`<div class="ss">${sub}</div>`:''}</div></div>`;
 const piece=(k,icon,label,injK)=>{const x=G(L[k]);
  if(!x)return tile(icon,label,k==='undersuit'?'none':'none worn','',injK?inj(...injK):'',true);
  const chip=x[2]?`<span class="cls cls${x[2]}">${x[2]}</span>`:'';
  const sub=[x[3]!=null?x[3]+'% dmg':'',x[4]!=null?x[4]+' / '+x[5]+' °C':''].filter(Boolean).join(' · ');
  const nm=x[0].replace(new RegExp('\\s+'+({helmet:'Helmet',core:'Core',arms:'Arms',legs:'Legs',undersuit:'Undersuit'}[k]||'#')+'\\b'),'');
  return tile(icon,label,esc(nm),sub,chip+(injK?inj(...injK):'')).replace('<div class="sn">',`<div class="sn" title="${esc(x[0])}">`)};
 $('#slotsL').innerHTML=piece('helmet','s-helmet','HELMET',['h'])+piece('core','s-core','CORE',['t'])+piece('arms','s-arms','ARMS',['la','ra'])+piece('legs','s-legs','LEGS',['ll','rl'])+piece('undersuit','s-suit','UNDERSUIT');
 const bp=S.bp, fits=!bp||(bp[11]||0)<=S.bpMax;
 const bpTile=S.bpMax||bp?tile('s-pack','BACKPACK',bp?esc(bp[0].replace(/\s+Backpack\b/,'')):'none',bp?(bp[7]!=null?`size ${bp[11]} · ${fmtU(bp[7])} µSCU${fits?'':' · too big for this core'}`:''):`fits up to size ${S.bpMax}`,'',!bp):tile('s-pack','BACKPACK','no backpack port','',' ',true);
 const wt=S.weap.map(([slot,max])=>{const w=L.w[slot];return tile(slot==='Sidearm'?'i-weapon':'i-weapon',slot.toUpperCase(),w?esc(w[0]):'empty',w?(w[1]||''):`size ${max} slot`,'',!w)}).join('');
 const hand=tile('s-tool','IN HAND',L.hand?esc(L.hand[0]):'—',L.hand?L.hand[1]:'','',!L.hand);
 $('#slotsR').innerHTML=bpTile+wt+hand;
 const p=(k,d)=>`<path d="${d}" fill="${s[k]==='ok'?'#0f2a33':col[s[k]]+'55'}" stroke="${s[k]==='ok'?'#5bd1e6':col[s[k]]}" stroke-width="1.6"/>`;
 $('#dollFig').innerHTML=bodySvg(s,'dollsvg',{fill:v=>v==='ok'?'#0f2a33':col[v]+'55',stroke:v=>v==='ok'?'#5bd1e6':col[v],sw:1.6,glow:true}).replace('</svg>','<text x="6" y="228" font-size="10" fill="#5f8a96">R</text><text x="108" y="228" font-size="10" fill="#5f8a96">L</text></svg>')+`<div class="dollnote">${Object.entries(s).filter(([k,v])=>v!=='ok').map(([k,v])=>`<span style="color:${col[v]}">${{la:'Left arm',ra:'Right arm',ll:'Left leg',rl:'Right leg',t:'Torso',h:'Head'}[k]} · ${word[v]}</span>`).join('<br>')||'No injuries'}</div>`;
 const c=(icon,name,n,cap)=>cap?`<div class="ctile cut ${n>cap?'over':''}">${ic(icon,'ic sm')}<span>${name}</span><b>${n} / ${cap}</b></div>`:'';
 $('#carry').innerHTML=c('s-mag','Magazines','—',S.mag)+c('s-gren','Throwables','—',S.gren)+c('v-hp','MedPens','—',S.med)+c('v-o2','OxyPens','—',S.oxy)+c('s-tool','Utility','—',S.util);
 const sp=$('#suitPanel'); if(sp){
  const pr=k=>k;
  sp.innerHTML=`<div class="ph">SUIT<span class="r">${esc(L.label)}</span></div>
   <div class="kv">TEMP RATING <b>${R.tmin==null?'—':R.tmin+' / '+R.tmax+' °C'}</b></div>
   ${R.coldP?`<div class="unit" style="margin:-2px 0 4px">weakest area: ${pr(R.coldP)}${R.hotP!==R.coldP?' for cold, '+pr(R.hotP)+' for heat':''}</div>`:''}
   <div class="kv">OUTSIDE <b style="color:${T.ok?'var(--g)':'#ff8a94'}">${T.txt}</b></div>
   <div class="kv">RADIATION <b>${R.rad==null?'—':R.rad.toLocaleString()+' REM'}</b></div>
   <div class="kv">CARRY <b>${R.carry==null?'—':fmtU(R.carry)+' µSCU'}${R.bpCap?' + '+fmtU(R.bpCap)+' pack':''}</b></div>
   <div class="suitscale">${(()=>{const t=seat==='eva'?footEnv().temp:outT,z=x=>Math.max(0,Math.min(1,(x+250)/400)),ok=t>=R.tmin&&t<=R.tmax;
    return vbox('OUTSIDE · SUIT RANGE',t,'°C',ok?'#eafcff':'#ff5468',z(t),[[0,z(R.tmin),ZR],[z(R.tmin),z(R.tmax),ZT],[z(R.tmax),1,ZR]])})()}</div>`}
}

/* ---- phone MAIN ---- */
function miniShip(){const bubble=((SH().caps||{}).sys||{}).faces!=='Quadrant';const SF=MODEL.shields||{},fc=SF.faces,f=bubble||!fc?(v=>({F:v,R:v,L:v,B:v}))(SF.bubble==null?null:SF.bubble):{F:fc[0],R:fc[1],B:fc[2],L:fc[3]};
 const G=shipGeo(shipK),hull=MODEL.hull,pad=11*G.u;
 return `<svg viewBox="${G.cx-G.rx-pad} ${G.cy-G.ry-pad} ${2*(G.rx+pad)} ${2*(G.ry+pad)}" class="msh">${defMarkup(G,f,hull,bubble,false,'hullclipm')}</svg>`}
let phMapBig=false;
function phoneRender(){applyShip();
 const el=$('#phmain'); if(!el)return; if(!phone){el.innerHTML='';return}
 const A=window.AS||{}, v=VIT(), tall=app.classList.contains('phtall'), short=app.classList.contains('phshort');
 const SFm=MODEL.shields||{},tot=SFm.bubble!=null?Math.round(SFm.bubble):SFm.faces?Math.round(SFm.faces.reduce((a,b)=>a+b,0)/SFm.faces.length):null, hull=MODEL.hull, open=needs.filter(n=>!n.done).length;
 const lights=Object.entries(A).filter(([k,s])=>s==='min'||s==='maj').map(([k,s])=>`<span class="pl ${s}">${k}</span>`).join('')||'<span class="pl ok">ALL SYSTEMS OK</span>';
 const loc=`<button class="phrow" data-go="nav">${ic('t-nav','ic sm')}<span><b>${esc((MODEL.log||{}).place||'—')}</b> ${(MODEL.log||{}).armistice===true?'<span class="zoneA">Armistice</span>':''}</span><span class="unit" style="margin-left:auto">▸</span></button>`;
 const pm=PM(), po=pm?curObj(pm):null;
 const msn=!pm?`<button class="phrow" data-go="msn">${ic('i-obj','ic sm')}<span class="unit">No contracts yet</span></button>`:`<button class="phrow" data-go="msn">${ic('i-obj','ic sm')}<span class="mnum s">${pm.n}</span><span style="min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(po[0])}</span>${po[2]?`<span class="unit" style="margin-left:auto">${po[2].replace(/ /g,'')}</span>`:''}<span class="unit">${MISSIONS.length} active</span></button>`;
 const EV=foot?null:envCard(), env=EV?`<button class="phrow env ${EV[0]}" data-go="you">${ic('v-air','ic sm')}<span><b>${EV[1].split(' · ')[0]}</b></span><span class="unit" style="margin-left:auto">${EV[2].temp==null?'space':EV[2].temp+' °C'} · ${EV[2].grav.toFixed(1)} g</span></button>`:'';
 const wait=open?`<button class="phrow wait" data-go="feed">${ic('i-party','ic sm')}<span><b>${open} waiting</b> · ${needs.filter(n=>!n.done).map(n=>NTYPE[n.type][1].split(' ')[0]).join(', ')}</span><span class="unit" style="margin-left:auto">▸</span></button>`:'';
 const pfight=combat&&!pinMap&&(seat==='pilot'||seat==='gunner');
 const mapstrip=pfight?`<div class="phdmg"><div class="phdh"><span>DAMAGE</span><button class="mapbtn" id="phMapPin">MAP</button></div>${phDmgRows(tot,hull).map(r=>`<button class="phdr ${r[0]}" data-go="sys"><span class="phdn">${r[0]==='maj'?'▲':'●'} ${r[1]}</span><span class="phdd">${r[2]}</span>${r[3]!=null?`<span class="phbar"><i style="width:${r[3]}%"></i></span>`:''}</button>`).join('')}<div class="dguns ph" data-guns>${gunsInner()}</div><div class="phdok">${Object.values(MODEL.ann||{}).filter(l=>l==='ok').length} systems OK</div></div>`
  :`<div class="phmap"><div class="phmhead"><button data-z="sys" class="${mapMode==='sys'?'on':''}">SYSTEM</button><button data-z="loc" class="${mapMode==='loc'?'on':''}">LOCAL</button><span class="phmsys">${SYSN[mapView]}</span><button class="phexp">${phMapBig?'CLOSE':'EXPAND'}</button></div><div class="phsvg">${$(mapMode==='loc'?'#mapLoc':'#mapSys').outerHTML.replace(/ id="map(Sys|Loc)"/,'').replace(/ style="[^"]*"/,'')}</div>${phMapBig?`<div class="phdist">${$('.mapwrap .dist').innerHTML}</div>`:''}${combat&&pinMap&&seat!=='foot'&&seat!=='eva'?'<button class="mapbtn phback" id="phDmgBack">DAMAGE</button>':''}</div>`;
 let glance='', deckIds=[];
 if(seat==='pilot'||seat==='gunner'){
  const left=seat==='pilot'?`<div class="phspd"><span class="big">${$('#spd').textContent}</span><span class="unit">m/s · ${$('#mode').textContent}</span><span class="unit">G ${$('#g').textContent.split(' /')[0]} · H₂ ${MODEL.flight.h2==null?'—':Math.round(MODEL.flight.h2)+'%'}</span></div>`
   :`<div class="phspd"><span class="big" style="font-size:26px">${bigShip&&myTurretObj()?myTurretObj().n.toUpperCase():'TURRET'}</span><span class="unit">${weapons.length} guns · ${weapons.filter(w=>!(w[2]&&w[3])).length} not ready</span><span class="unit">${MODEL.cm.decoy??'—'} decoys · ${MODEL.cm.noise??'—'} noise</span></div>`;
  glance=`<div class="phglance">${left}<div class="phdef">${miniShip()}<div class="phdv"><span style="color:${tot!=null&&tot<60?'#ffb347':'#5bd1e6'}">${tot==null?'—':tot+'%'}</span><span class="unit">SHIELD</span><span style="color:${hull!=null&&hull<70?'#ffb347':'#5be69a'}">${hull==null?'—':hull+'%'}</span><span class="unit">HULL</span></div></div></div><div class="phlights">${lights}</div>`;
 }else{
  const vb=seat==='eva'?['o2','hp','food','water']:['hp','o2','food','water'];
  glance=`<div class="phglance foot"><div class="vgrid ph">${vb.map(k=>vitBox(k)).join('')}</div><div class="figwrap" data-fig></div></div>
   <button class="phrow" data-go="you">${ic('i-shield','ic sm')}<span>${esc([LIVESET().label,...Object.values(LIVESET().w).map(x=>x[0])].filter(Boolean).join(' · ')||'No loadout in the log yet')}</span><span class="unit" style="margin-left:auto">▸</span></button>
   ${short?'':`<div class="phrow dim">${ic('t-ship','ic sm')}<span>${esc(SH().short||'—')}${LASTSHIP.hull!=null?' · hull '+LASTSHIP.hull+'%':''}${LASTSHIP.sh!=null?' · shields '+LASTSHIP.sh+'%':''}</span><span class="unit" style="margin-left:auto">${LASTSHIP.t?new Date(LASTSHIP.t).toTimeString().slice(0,5):''}</span></div>`}`;
  deckIds=[];
 }
 const pdeck=(seat==='pilot'||seat==='gunner')?deckHtml(8):''; const pcols=seat==='gunner'?(pdeck.match(/data-id=/g)||[]).length:4;
 const emotes='';
 el.classList.toggle('mapbig',phMapBig&&!pfight);
 const tight=short&&pfight; el.classList.toggle('tight',tight);
 el.innerHTML=glance+mapstrip+loc+env+(tight?'':msn)+wait+(pdeck?`<div class="phdeck" style="grid-template-columns:repeat(${pcols},1fr)">${pdeck}</div>`:'')+emotes;
 const pp=el.querySelector('#phMapPin'); if(pp)pp.onclick=()=>{pinMap=true;render()}; const pb2=el.querySelector('#phDmgBack'); if(pb2)pb2.onclick=()=>{pinMap=false;render()};
 el.querySelectorAll('[data-go]').forEach(x=>x.onclick=()=>document.querySelector(`#bezel .osb[data-p="${x.dataset.go}"]`).click());
 const pd=el.querySelector('.phdeck'); if(pd&&pdeck)bindDeck(pd);
 fig();
}
$('#moreBtn').onclick=()=>{let sh=$('#moreSheet');if(sh){sh.remove();return}sh=document.createElement('div');sh.id='moreSheet';sh.className='panel cut';
 sh.innerHTML=['sys','ppl','you'].map(p=>`<button class="osb cut" data-more="${p}">${{sys:'SYS',ppl:'PEOPLE',you:'YOU'}[p]}</button>`).join('');app.appendChild(sh);
 sh.querySelectorAll('[data-more]').forEach(b=>b.onclick=()=>{document.querySelector(`#bezel .osb[data-p="${b.dataset.more}"]`).click();sh.remove()})};
$$('.mapmodes button').forEach(b=>b.onclick=()=>{$$('.mapmodes button').forEach(x=>x.classList.toggle('on',x===b));$('#mapSys').style.display=b.dataset.z==='sys'?'':'none';$('#mapLoc').style.display=b.dataset.z==='loc'?'':'none'});
addEventListener('resize',layout);

/* ---- live tick ---- */
/* SC time: UTC date and time, year +930, Standard Earth Time */
function scTime(){const d=new Date(),z=n=>String(n).padStart(2,'0');return `${d.getUTCFullYear()+930}-${z(d.getUTCMonth()+1)}-${z(d.getUTCDate())} · ${z(d.getUTCHours())}:${z(d.getUTCMinutes())} SET`}
function tick(){const pc=$('#pclk');if(pc)pc.textContent=new Date().toTimeString().slice(0,5);const c=$('#clk');if(!c)return;c.textContent=c.classList.contains('sct')?scTime():new Date().toTimeString().slice(0,8)}
function liveTick(){
 tick();
 $('#spd')&&($('#spd').textContent=SNOW);speedo(SNOW);
 $('#g')&&($('#g').textContent=GNOW==null?'—':GNOW.toFixed(1));$('#gmax')&&($('#gmax').textContent=MODEL.flight.gMax?' / '+MODEL.flight.gMax.toFixed(1):'');
 instruments();hud();suitHud();
 $$('[data-guns]').forEach(e=>{if(!e.closest('.zclone'))e.innerHTML=gunsInner()});gunMarks();
 rings();
 $('#hr')&&($('#hr').textContent=hrNow+' bpm');$('#hrb')&&($('#hrb').style.width=Math.min(100,Math.max(0,hrNow-30))+'%');
 $$('.hrx').forEach(e=>e.textContent=hrNow?hrNow+' bpm':'—');
 const pb=document.querySelector('.phspd .big'); if(phone&&seat==='pilot'&&pb)pb.textContent=SNOW;
}



/* ---- star map: positions from scunpacked-data starmap_positions.json (4.10.3-LIVE), trimmed ---- */
const STARMAP={"nyx":[["Nyx","star","",0.0,0.0],["Pyro Gateway","gate","",-12.0,20.7846],["Nyx II","planet","",-3.5912,9.8668],["Stanton Gateway","gate","",-13.4999,-23.3827],["People's Service Station Theta","station","Nyx",40.4727,-25.8063],["Levski","station","Nyx",-9.6417,-11.4907],["People's Service Station Alpha","station","Nyx",-45.8043,-14.3513],["People's Service Station Delta","station","Nyx",-6.4325,-47.567],["People's Service Station Lambda","station","Nyx",43.3278,20.6568],["Nyx I","planet","",6.4952,-3.75],["Nyx III","planet","",-29.1097,-24.4259]],"pyro":[["Pyro","star","",0.0,0.0],["Pyro V","planet","",42.2853,-7.4546],["Adir","moon","Pyro V",42.2638,-7.3557],["Ignis","moon","Pyro V",42.2969,-7.5086],["Pyro IV","planet","",42.6145,-7.273],["Vatra","moon","Pyro V",42.2167,-7.4262],["Vuur","moon","Pyro V",42.1644,-7.5825],["Fairo","moon","Pyro V",42.4031,-7.4985],["Fuego","moon","Pyro V",42.3645,-7.3284],["Gaslight","station","Pyro",46.4944,-8.1909],["Starlight Service Station","station","Pyro",-7.7566,-14.0089],["Patch City","station","Pyro",11.4453,13.6223],["Megumi Refueling","station","Pyro",-59.1456,34.1455],["Endgame","station","Pyro",48.2968,48.292],["Pyro I","planet","",1.72,-8.092],["Rod's Fuel 'N Supplies","station","Pyro",14.6643,40.3386],["Ruin Station","station","Terminus",-52.3368,-43.9179],["Terminus","planet","",-52.3365,-43.9182],["Dudley & Daughters","station","Pyro",23.3426,-64.226],["Monox","planet","",9.1984,5.3107],["Checkmate","station","Pyro",1.8334,10.4345],["Orbituary","station","Bloom",-8.9004,-15.4178],["Bloom","planet","",-8.9008,-15.4171],["Rat's Nest","station","Pyro",3.7326,-42.7669],["Nyx Gateway","gate","",32.994,-38.1517],["Stanton Gateway","gate","",-37.6092,37.4357]],"stanton":[["Stanton","star","",0.0,0.0],["Everus Harbor","station","Hurston",12.8499,-0.0009],["Lorville","city","Hurston",12.8501,-0.0008],["Hurston","planet","",12.8505,0.0],["Arial","moon","Hurston",12.8927,-0.0315],["Aberdeen","moon","Hurston",12.9058,0.041],["Ita","moon","Hurston",12.8302,0.1149],["Magda","moon","Hurston",12.7927,-0.0745],["Baijini Point","station","ArcCorp",18.5869,-22.1522],["Area18","city","ArcCorp",18.5869,-22.152],["ArcCorp","planet","",18.5877,-22.1519],["Wala","moon","ArcCorp",18.3796,-22.0005],["Lyria","moon","ArcCorp",18.7036,-22.1217],["Port Tressler","station","microTech",22.4626,37.1863],["New Babbage","city","microTech",22.4626,37.1862],["microTech","planet","",22.4621,37.1857],["Calliope","moon","microTech",22.5258,37.2026],["Euterpe","moon","microTech",22.4361,37.2904],["Clio","moon","microTech",22.4474,37.2805],["Nyx Gateway","gate","",-62.2843,23.4676],["Seraphim Station","station","Crusader",-18.9563,-2.6663],["Orison","city","Crusader",-18.9569,-2.6658],["Crusader","planet","",-18.9622,-2.665],["Grim HEX","station","Yela",-19.0235,-2.6136],["Yela","moon","Crusader",-19.0229,-2.614],["Daymar","moon","Crusader",-18.9305,-2.6102],["Covalex Shipping Hub Gundo","station","Daymar",-18.9308,-2.6097],["Cellin","moon","Crusader",-18.9876,-2.709],["Security Post Kareah","station","Cellin",-18.9876,-2.7094],["Wikelo Emporium Dasi Station","station","Stanton",14.3334,2.0012],["Pyro Gateway","gate","",3.3105,-27.9794],["Wikelo Emporium Selo Station","station","Stanton",-20.1566,-1.2631],["Wikelo Emporium Kinga Station","station","Stanton",23.7614,39.2254],["Terra Gateway","gate","",51.1182,-5.27],["HUR-L1","lstation","",11.5614,-0.0003],["HUR-L5","lstation","",6.4257,-11.1284],["CRU-L4","lstation","",-7.1738,-17.7501],["HUR-L3","lstation","",-12.8447,-0.0016],["ARC-L5","lstation","",-9.8958,-27.1752],["HUR-L2","lstation","",14.1396,-0.002],["CRU-L5","lstation","",-11.785,15.0912],["MIC-L2","lstation","",24.7138,40.9129],["HUR-L4","lstation","",6.4276,11.1248],["ARC-L2","lstation","",20.451,-24.3681],["ARC-L4","lstation","",28.4761,5.0189],["ARC-L1","lstation","",16.7292,-19.942],["ARC-L3","lstation","",-25.0455,14.4633],["MIC-L1","lstation","",20.2158,33.4671],["CRU-L1","lstation","",-17.0688,-2.3995],["MIC-L5","lstation","",43.4486,-0.8577],["MIC-L4","lstation","",-20.9687,38.0561],["MIC-L3","lstation","",-22.4522,-37.1799]]};
const SYSN={stanton:'STANTON',pyro:'PYRO',nyx:'NYX'};
let HERE={sys:'stanton',name:''}, OBJ={sys:'stanton',name:''};
const PCOL={Hurston:'#a98a62',Crusader:'#6b8fb5',ArcCorp:'#b06a4a',microTech:'#9fc6d6','Pyro I':'#c07a4a',Monox:'#8a8f7a',Bloom:'#6f9a6a','Pyro IV':'#a88a6a','Pyro V':'#c9a46a',Terminus:'#7a8aa0','Nyx I':'#8f9aa8','Nyx II':'#6f8fa0','Nyx III':'#9a8fb0'};
const LCODE={HUR:'Hurston',CRU:'Crusader',ARC:'ArcCorp',MIC:'microTech'};
let mapView='stanton', mapMode='sys';
const ent=(s,n)=>(STARMAP[s]||[]).find(r=>r[0]===n);
const anc=x=>x>455?'end':x<145?'start':'middle';
const gm=(a,b)=>Math.hypot(a[3]-b[3],a[4]-b[4]);
const esc=t=>String(t).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/'/g,'&#39;');
function objDist(){const a=ent(HERE.sys,HERE.name),b=ent(OBJ.sys,OBJ.name);return a&&b&&HERE.sys===OBJ.sys?Math.round(gm(a,b)):null}
function youMark(x,y,lab,dy){const pn=partyNear(), pd=Array.from({length:pn},(_,i)=>{const a=-Math.PI/2+i*0.9;return `<circle cx="${(Math.cos(a)*13).toFixed(1)}" cy="${(Math.sin(a)*13).toFixed(1)}" r="2.6" fill="#5be69a" stroke="#06121a"/>`}).join('');
 return `<g transform="translate(${x} ${y})">${pd}</g>`+(pn?`<text x="${x+16}" y="${y-10}" font-size="10" fill="#5be69a">+${pn} party</text>`:'')+`<g transform="translate(${x} ${y})"><circle r="16" fill="none" stroke="#5bd1e6" stroke-opacity=".35"><animate attributeName="r" values="8;20;8" dur="2.4s" repeatCount="indefinite"/></circle><circle r="7" fill="none" stroke="#5bd1e6" stroke-width="2"/><circle r="3" fill="#5bd1e6"/></g><text x="${x}" y="${y+(dy||24)}" font-size="12" fill="#eafcff" text-anchor="middle">${esc(lab)}</text>`}
function objMark(x,y,lab){return `<g transform="translate(${x} ${y})"><path d="M0 -9 L9 0 L0 9 L-9 0Z" fill="none" stroke="#ffb347" stroke-width="2"/><circle r="2.5" fill="#ffb347"/></g><text x="${x}" y="${y-14}" font-size="12" fill="#ffb347" text-anchor="middle">${esc(lab)}</text>`}
function drawSystem(s){
 const E=STARMAP[s]||[], cx=300, cy=210;
 const far=Math.max(...E.filter(r=>r[1]!=='moon'&&r[1]!=='city'&&r[1]!=='gate').map(r=>Math.hypot(r[3],r[4])));
 const rad=d=>176*Math.sqrt(d/far);
 const P=r=>{const a=Math.atan2(r[4],r[3]);if(r[1]==='gate')return [cx+Math.cos(a)*200, cy-Math.sin(a)*188];const q=rad(Math.hypot(r[3],r[4]));return [cx+Math.cos(a)*q, cy-Math.sin(a)*q]};
 let o=`<defs><radialGradient id="sun"><stop offset="0" stop-color="#fff2c4"/><stop offset=".5" stop-color="#ffd27a"/><stop offset="1" stop-color="#ffd27a" stop-opacity="0"/></radialGradient></defs>`;
 o+=`<g fill="none" stroke="#163a48">${E.filter(r=>r[1]==='planet').map(r=>`<circle cx="${cx}" cy="${cy}" r="${rad(Math.hypot(r[3],r[4])).toFixed(1)}"/>`).join('')}</g>`;
 o+=`<circle cx="${cx}" cy="${cy}" r="22" fill="url(#sun)"/><circle cx="${cx}" cy="${cy}" r="7" fill="#ffe7a8"/>`;
 o+=`<g fill="#2f5866">${E.filter(r=>r[1]==='lstation').map(r=>{const[x,y]=P(r);return `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="2.2"><title>${esc(r[0])}</title></circle>`}).join('')}</g>`;
 const a=s===HERE.sys&&ent(s,HERE.name), b=s===OBJ.sys&&ent(s,OBJ.name);
 const PLS=E.filter(r=>r[1]==='planet').map(P);
 const OFF=r=>{const[x,y]=P(r);return PLS.some(([px,py])=>Math.hypot(px-x,py-y)<16)?[x-16,y-16,x,y]:[x,y,x,y]};
 const BQ=b?OFF(b):null, placed=[...(BQ?[[BQ[0],BQ[1]]]:[]),...(a?[P(a)]:[])];
 const PINS=MISSIONS.filter(m=>m.place&&m.place[0]===s&&m.n!==pinned).map(m=>{const r=ent(s,m.place[1]);if(!r)return null;const q=OFF(r);let x=q[0];while(placed.some(([px,py])=>Math.hypot(px-x,py-q[1])<18))x-=18;placed.push([x,q[1]]);return {m,q:[x,q[1],q[2],q[3]]}}).filter(Boolean);
 const under=(x,y,t)=>{const w=t.length*5.2,an=anc(x),x0=an==='start'?x:an==='end'?x-w:x-w/2,x1=x0+w,y0=y-15,y1=y-4;return PINS.some(p=>p.q[0]+9>x0&&p.q[0]-9<x1&&p.q[1]+9>y0&&p.q[1]-9<y1)||(BQ&&BQ[0]+10>x0&&BQ[0]-10<x1&&BQ[1]+10>y0&&BQ[1]-10<y1)};
 const stLab=n=>n.replace(/^Wikelo Emporium (\w+) Station$/,'Wikelo $1').replace("People's Service Station","PSS").replace(" Service Station","").replace(" Refueling","").replace(" 'N Supplies","");
 o+=`<g fill="#4f8796" font-size="9" text-anchor="middle">${E.filter(r=>r[1]==='station'&&(!r[2]||(ent(s,r[2])||[])[1]==='star')).map(r=>{const[x,y]=P(r);if(under(x,y,stLab(r[0])))return `<rect x="${(x-2.5).toFixed(1)}" y="${(y-2.5).toFixed(1)}" width="5" height="5"><title>${esc(r[0])}</title></rect>`;return `<rect x="${(x-2.5).toFixed(1)}" y="${(y-2.5).toFixed(1)}" width="5" height="5"/><text x="${x.toFixed(1)}" y="${(y-6).toFixed(1)}" fill="#5f97a6" text-anchor="${anc(x)}">${esc(r[0].replace(/^Wikelo Emporium (\w+) Station$/,'Wikelo $1').replace("People's Service Station","PSS").replace(" Service Station","").replace(" Refueling","").replace(" 'N Supplies",""))}</text>`}).join('')}</g>`;
 o+=E.filter(r=>r[1]==='gate').map(r=>{const[x,y]=P(r);const to=r[0].replace(' Gateway',''), mc=MISSIONS.filter(m=>m.place&&m.place[0]===to.toLowerCase()).length;return `<g transform="translate(${x.toFixed(1)} ${y.toFixed(1)})"><path d="M0 -7 L7 0 L0 7 L-7 0Z" fill="#0a1a22" stroke="#b58cff" stroke-width="1.5"/></g><text x="${x.toFixed(1)}" y="${(y+19).toFixed(1)}" font-size="10" fill="#b58cff" text-anchor="${anc(x)}" letter-spacing=".08em">→ ${esc(to.toUpperCase())}${mc?` · ${mc} MSN`:''}</text>`}).join('');
 o+=`<g font-size="12" fill="#8cc9d6" text-anchor="middle">${E.filter(r=>r[1]==='planet').map(r=>{const[x,y]=P(r);return `<circle data-local="${esc(r[0])}" style="cursor:pointer" cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="8" fill="${PCOL[r[0]]||'#7a8a96'}"/><text x="${x.toFixed(1)}" y="${(y+22).toFixed(1)}">${esc(r[0])}</text>`}).join('')}</g>`;
 const lead=([x,y,ox,oy])=>x!==ox?`<line x1="${ox}" y1="${oy}" x2="${x}" y2="${y}" stroke="#c8913f" stroke-width="1"/>`:'';
 if(a&&b){const[x1,y1]=P(a),[x2,y2]=OFF(b);o+=`<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="#ffb347" stroke-width="2" stroke-dasharray="6 5"/>`}
 o+=PINS.map(({m,q})=>lead(q)+numMark(q[0],q[1],m.n)).join('');
 if(b){const q=OFF(b),[x,y]=q;o+=lead(q);o+=objMark(x,y,pinned+' · '+shortPlace(OBJ.name)+(a?' · '+objDist()+' Gm':''))}
 if(a){const[x,y]=P(a),near=E.some(r=>r[1]==='planet'&&Math.hypot(P(r)[0]-x,P(r)[1]-y)<40);o+=youMark(x,y,'YOU · '+HERE.name,near?38:24)}
 if(s==='nyx')o+=`<text x="300" y="414" font-size="11" fill="#7aa3ae" text-anchor="middle" letter-spacing=".1em">OPENS WITH 5.0</text>`;
 return o;
}
let localAnchor=null;
function anchorFor(s){
 if(localAnchor&&ent(s,localAnchor))return localAnchor;
 if(s===HERE.sys){const h=ent(s,HERE.name);if(h){if(h[1]==='planet')return h[0];const c=LCODE[h[0].slice(0,3)];if(c)return c;let p=h[2];while(p){const q=ent(s,p);if(!q||q[1]==='planet')return p;p=q[2]}}}
 const pl=(STARMAP[s]||[]).filter(r=>r[1]==='planet');
 return (pl.map(p=>[p[0],(STARMAP[s]).filter(r=>r[2]===p[0]).length]).sort((x,y)=>y[1]-x[1])[0]||[''])[0];
}
function drawLocal(s){
 const E=STARMAP[s]||[], pn=anchorFor(s), pl=ent(s,pn); if(!pl)return '';
 const cx=300, cy=205, kids=E.filter(r=>r[2]===pn||(ent(s,r[2])||[])[2]===pn);
 const code=Object.keys(LCODE).find(c=>LCODE[c]===pn);
 const lps=(code?E.filter(r=>r[1]==='lstation'&&r[0].startsWith(code+'-')):[]).concat(E.filter(r=>r[1]==='station'&&(ent(s,r[2])||[])[1]==='star'&&gm(r,pl)<4));
 const dmax=Math.max(.001,...kids.map(r=>gm(r,pl)));
 const R=d=>34+118*Math.sqrt(d/dmax);
 const P=r=>{const d=gm(r,pl),a=Math.atan2(r[4]-pl[4],r[3]-pl[3]),rr=R(d);return [cx+Math.cos(a)*rr, cy-Math.sin(a)*rr*.85]};
 const RING=r=>{const d=gm(r,pl);return d<3?[150,134]:d<6?[172,156]:[196,180]};
 const PL=r=>{const a=Math.atan2(r[4]-pl[4],r[3]-pl[3]),[rx,ry]=RING(r);return [cx+Math.cos(a)*rx, cy-Math.sin(a)*ry]};
 let o=`<circle cx="${cx}" cy="${cy}" r="26" fill="${PCOL[pn]||'#7a8a96'}" fill-opacity=".55" stroke="${PCOL[pn]||'#7a8a96'}"/><text x="${cx}" y="${cy+44}" font-size="13" fill="#8cc9d6" text-anchor="middle">${esc(pn)}</text>`;
 o+=`<g fill="none" stroke="#163a48">${kids.filter(r=>r[1]==='moon').map(r=>`<ellipse cx="${cx}" cy="${cy}" rx="${R(gm(r,pl)).toFixed(1)}" ry="${(R(gm(r,pl))*.85).toFixed(1)}"/>`).join('')}</g>`;
 o+=`<g font-size="11" fill="#8cc9d6" text-anchor="middle">`+kids.filter(r=>r[1]==='moon').map(r=>{const[x,y]=P(r);return `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="6" fill="#7d8a96"/><text x="${x.toFixed(1)}" y="${(y-11).toFixed(1)}">${esc(r[0])}</text>`}).join('')+`</g>`;
 let onP=0; const stackY={};
 o+=`<g font-size="10" fill="#5bd1e6" text-anchor="middle">`+kids.filter(r=>r[1]==='station'||r[1]==='city').map(r=>{const par=ent(s,r[2]);const nm=esc(r[0].replace('Covalex Shipping Hub ','Covalex '));
  if(par&&par[1]==='moon'){const[x,y]=P(par);return `<circle cx="${x.toFixed(1)}" cy="${(y+12).toFixed(1)}" r="2.5"/><text x="${x.toFixed(1)}" y="${(y+24).toFixed(1)}">${nm}</text>`}
  const ty=cy+60+14*(onP++);stackY[r[0]]=[ty,r[0].length];return `<text x="${cx}" y="${ty}">${r[1]==='city'?'◆ ':'▪ '}${nm}</text>`}).join('')+`</g>`;
 o+=`<g font-size="10" fill="#7aa3ae" text-anchor="middle">`+lps.map(r=>{const[x,y]=PL(r);return `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="3" fill="#2f5866"/><text x="${x.toFixed(1)}" y="${(y+(RING(r)[0]<196?-7:15)).toFixed(1)}" text-anchor="${anc(x)}">${esc(shortPlace(r[0]))} · ${Math.round(gm(r,pl)*10)/10} Gm</text>`}).join('')+`</g>`;
 const off=[];
 o+=MISSIONS.filter(m=>m.place&&m.place[0]===s).map(m=>{const r=ent(s,m.place[1]);if(!r)return '';let xy=null;
  if(lps.includes(r)){const inner=RING(r)[0]<196;xy=PL(r).map((v,i)=>i?v+(inner?12:-14):v+14)}
  else if(r[1]==='moon'&&r[2]===pn)xy=P(r).map((v,i)=>i?v-10:v+14);
  else if(r[1]==='station'||r[1]==='city'){const par=ent(s,r[2]);if(par&&par[1]==='moon'&&par[2]===pn)xy=P(par).map((v,i)=>i?v+12:v+16);else if(r[2]===pn&&stackY[r[0]]){const[ty,len]=stackY[r[0]];xy=[cx-(len*2.9+18),ty-4]}}
  if(xy){const pin=m.n===pinned;return pin?`<g transform="translate(${xy[0].toFixed(1)} ${xy[1].toFixed(1)})"><path d="M0 -11 L11 0 L0 11 L-11 0Z" fill="#0a1a22" stroke="#ffb347" stroke-width="2"/><text y="3.5" font-size="10" fill="#ffb347" text-anchor="middle">${m.n}</text></g>`:numMark(xy[0],xy[1],m.n)}
  const d=mDist(m);off.push({a:Math.atan2(r[4]-pl[4],r[3]-pl[3]),t:`${m.n} ${shortPlace(m.place[1])}${d!=null?' '+d+' Gm':''}`,c:m.n===pinned?'#ffb347':'#c8913f',pin:m.n});return ''}).join('');
 // planet picker
 const pls=E.filter(r=>r[1]==='planet');
 o+=`<g font-size="10" letter-spacing=".08em">`+pls.map((r,i)=>`<text class="lpick" data-local="${esc(r[0])}" x="${92+i*(420/Math.max(pls.length,1))}" y="16" fill="${r[0]===pn?'#eafcff':'#5f8a96'}" style="cursor:pointer;${r[0]===pn?'text-decoration:underline':''}">${esc(r[0].toUpperCase())}</text>`).join('')+`</g>`;
 const h=s===HERE.sys&&ent(s,HERE.name);
 if(h){const isL=lps.includes(h), inside=isL||h[0]===pn||kids.includes(h);
  if(inside){const[x,y]=isL?PL(h):(h[0]===pn?[cx,cy]:P(h));o+=youMark(x,y-(isL?22:0),'YOU · '+HERE.name)}
  else off.unshift({a:Math.atan2(h[4]-pl[4],h[3]-pl[3]),t:`YOU ${HERE.name}`,c:'#5bd1e6'})}
 if(off.length){let x=88;o+=`<line x1="84" y1="392" x2="516" y2="392" stroke="#163a48"/><text x="88" y="406" font-size="9" fill="#5f8a96" letter-spacing=".12em">ELSEWHERE</text>`;x=150;
  o+=off.map(f=>{const w=f.t.length*5.4+22, g=`<g ${f.pin?`class="mpin" data-pin="${f.pin}" style="cursor:pointer"`:''}><g transform="translate(${x+5} 403) rotate(${(-f.a*180/Math.PI).toFixed(1)})"><path d="M-4 -4 L3 0 L-4 4" fill="none" stroke="${f.c}" stroke-width="1.8"/></g><text x="${x+14}" y="407" font-size="10" fill="${f.c}">${esc(f.t)}</text></g>`;x+=w;return g}).join('')}
 return o;
}
let PARTY=[], PARTYNEAR=0;
const partyNear=()=>PARTYNEAR;
let KILLS=[];
function silo(k,w,h){const S=SHAPE[k];if(!S)return ic('t-ship','ic');const sc=Math.min((w-6)/S[0],(h-6)/S[1]);
 return `<svg class="silo" viewBox="0 0 ${w} ${h}"><polygon points="${S[2].map(([x,y])=>`${(w/2+y*sc).toFixed(1)},${(h/2+x*sc).toFixed(1)}`).join(' ')}"/></svg>`}
function baseCls(c){return String(c||'').replace(/_\d+$/,'').replace(/_(PU_)?AI(_.*)?$/i,'')}
function pplShips(){const e=$('#pplShips');if(!e)return;const K=((MODEL.log||{}).kills||[]).slice().reverse();
 const n={dst:0,dis:0,crash:0};K.forEach(r=>{if(r.reason==='Collision')n.crash++;else if(r.level>=2)n.dst++;else n.dis++});
 const ago=m=>m<60?m+' min':Math.floor(m/60)+' h '+(m%60)+' min';
 e.innerHTML=`<div class="ph">SHIPS DESTROYED NEARBY<span class="r">this session · ${K.length}</span></div>`+(K.length?
  `<div class="ktal"><span class="other">DESTROYED<b>${n.dst}</b></span><span class="party">DISABLED<b>${n.dis}</b></span><span class="crash">CRASHED<b>${n.crash}</b></span></div>
  <div class="kills">${K.map(r=>{const b=baseCls(r.cls);loadShip(b);const npc=/(^|_)AI(_|$)/i.test(r.cls||''),crash=r.reason==='Collision',lv=r.level>=2?2:1,m=Math.max(0,Math.round((Date.now()/1000-r.t)/60));
   return `<div class="krow ${lv===2?'dst':'dis'}${m>10?' old':''}"><span class="sil">${silo(b,64,42)}</span>
    <span class="kn"><b>${esc(shipNameOf(b))}</b><small>${npc?'NPC':'player ship'} · ${crash?'collision':'combat'}</small></span>
    <span class="kst">${lv===2?'DESTROYED':'DISABLED'}</span>
    <span class="kby other">${crash?'crashed':`${ic('i-kill','ic sm')}<b>${esc(/^[A-Z]{3,4}_\w+_\d{6,}$/.test(r.killer||'')?shipNameOf(baseCls(r.killer)):(r.killer||'unknown'))}</b>`}</span>
    <span class="kt">${ago(m)}</span></div>`}).join('')}</div>`:'<div class="row unit">No ships destroyed nearby</div>');
 const st=(id,v)=>{const x=$('#'+id);if(x)x.textContent=v};st('pDst',String(n.dst));st('pDead',String(SESS.dead));st('pDown',String(SESS.downed))}
function drawMap(){
 const sv=$('#mapSys'), lv=$('#mapLoc'); if(!sv)return;
 sv.innerHTML=drawSystem(mapView); lv.innerHTML=drawLocal(mapView); sv.setAttribute('viewBox','80 0 440 420'); lv.setAttribute('viewBox','80 0 440 420');
 sv.style.display=mapMode==='sys'?'':'none'; lv.style.display=mapMode==='loc'?'':'none';
 const ns=$('#navSys'), nl=$('#navLoc'); if(ns){ns.innerHTML=sv.innerHTML;nl.innerHTML=lv.innerHTML;ns.style.display=sv.style.display;nl.style.display=lv.style.display}
 pplShips();
 $$('.mapp .sysbtn').forEach(b=>b.classList.toggle('on',b.dataset.sysn===mapView));
 $$('.mapp .mapmodes button').forEach(b=>b.classList.toggle('on',b.dataset.z===mapMode));
 const hereE=ent(HERE.sys,HERE.name), d=$('.dist'); if(!d)return;
 let rows='';
 if(mapView===HERE.sys&&hereE){
  rows+=MISSIONS.map(m=>{const d=mDist(m);return `<div class="dr dm ${m.n===pinned?'on':''}" data-pin="${m.n}"><span class="mnum s">${m.n}</span><span class="dmn">${esc(m.place?shortPlace(m.place[1]):'no location')}</span><b>${d!=null?d+' Gm':m.place?SYSN[m.place[0]][0]+SYSN[m.place[0]].slice(1).toLowerCase():'—'}</b></div>`}).join('')+`<div class="dh" style="margin-top:8px">PLANETS</div>`;
  rows+=(STARMAP[mapView]).filter(r=>r[1]==='planet').map(r=>[r,gm(r,hereE)]).sort((x,y)=>x[1]-y[1]).map(([r,g])=>`<div class="dr"><span class="sw" style="background:${PCOL[r[0]]||'#7a8a96'}"></span>${esc(r[0])}<b>${Math.round(g)} Gm</b></div>`).join('');
  rows+=`<div class="dr dzone"><span class="${(MODEL.log||{}).armistice?'zoneA':'zoneU'}" id="zoneTxt">${esc(zoneLabel())}</span><span class="mute">&nbsp;· ${esc(HERE.name)}</span></div>`;
 }else{
  rows=MISSIONS.filter(m=>m.place&&m.place[0]===mapView).map(m=>`<div class="dr dm ${m.n===pinned?'on':''}" data-pin="${m.n}"><span class="mnum s">${m.n}</span><span class="dmn">${esc(shortPlace(m.place[1]))}</span></div>`).join('')+`<div class="dr mute">You are in ${SYSN[HERE.sys][0]+SYSN[HERE.sys].slice(1).toLowerCase()}</div>`+(STARMAP[mapView]).filter(r=>r[1]==='gate').map(r=>`<div class="dr"><span class="sw" style="background:#b58cff"></span>${esc(r[0])}</div>`).join('');
 }
 d.innerHTML=`<div class="dh">MISSIONS</div>`+rows; const nd=$('#navDist'); if(nd)nd.innerHTML=d.innerHTML;
}
document.addEventListener('click',e=>{
 const sb=e.target.closest('.mapp .sysbtn'); if(sb){mapView=sb.dataset.sysn;localAnchor=null;drawMap();phoneRender();return}
 const lp=e.target.closest('.mapp [data-local], .phmap [data-local]'); if(lp&&!lp.closest('.zclone')){localAnchor=lp.dataset.local;mapMode='loc';drawMap();phoneRender();return}
 const pm=e.target.closest('.phmhead button[data-z]'); if(pm){mapMode=pm.dataset.z;drawMap();phoneRender();return}
 if(e.target.closest('.phexp')){phMapBig=!phMapBig;phoneRender();return}
 const mb=e.target.closest('.mapp .mapmodes button'); if(mb){mapMode=mb.dataset.z;setTimeout(drawMap,0)}
});

/* ---- active missions: all accepted contracts from Game.log; the pin is Live View's own ---- */
/* ---- missions: every accepted contract from Game.log; type from the contract name (CSR's mission_type rules);
   giver, rank, rep and blueprint rewards from CSR's sc_missions.json; progress only where the log carries it ---- */
const MTYPE={contract:['CONTRACT','i-contract'],bounty:['BOUNTY','i-cross'],combat:['COMBAT','i-shield'],haul:['HAULING','i-contract'],mining:['MINING','i-mine'],delivery:['DELIVERY','i-contract'],fps:['GROUND','i-weapon'],recover:['RECOVERY','i-scan'],training:['TRAINING','i-ready']};
let MISSIONS=[];
/* hauling and mining objectives are the log's own "Deliver a/b SCU of X to Y" lines */

let MHIST=[];
let pinned=1;
const PM=()=>MISSIONS.find(m=>m.n===pinned)||MISSIONS[MISSIONS.length-1]||null;
const shortPlace=n=>n.replace(/^Wikelo Emporium (\w+) Station$/,'Wikelo $1');
function mDist(m){if(!m||!m.place||m.place[0]!==HERE.sys)return null;const a=ent(HERE.sys,HERE.name),b=ent(m.place[0],m.place[1]);return a&&b?Math.round(gm(a,b)):null}
function mWhere(m){if(!m.place)return m.placeName?m.placeName+' · not on the map':'no location';const d=mDist(m);return d!=null?`${shortPlace(m.place[1])} · ${d} Gm`:`${shortPlace(m.place[1])} · ${SYSN[m.place[0]][0]+SYSN[m.place[0]].slice(1).toLowerCase()}`}
function curObj(m){return m.obj.find(o=>o[1]===1)||m.obj[0]}
function pin(n){pinned=n;const m=PM();if(!m){render();return}if(m.place){OBJ.sys=m.place[0];OBJ.name=m.place[1]}else{OBJ.sys=null;OBJ.name=''}render()}
function numMark(x,y,n){return `<g class="mpin" data-pin="${n}" style="cursor:pointer" transform="translate(${x.toFixed(1)} ${y.toFixed(1)})"><circle r="8" fill="#0a1a22" stroke="#c8913f" stroke-width="1.5"/><text y="3.5" font-size="10" fill="#ffcf8a" text-anchor="middle">${n}</text></g>`}
/* one short metric per type, for lists and the MAIN card */
function mMetric(m){if(m.live)return [m.time,null];const sc=m.drops?m.drops.reduce((a,x)=>[a[0]+x[1],a[1]+x[2]],[0,0]):null;
 if(m.type==='haul'||m.type==='mining')return [`${sc[0]}/${sc[1]} SCU`,sc[0]/sc[1]];
 if(m.type==='delivery')return [`${m.boxes[0]}/${m.boxes[1]} boxes`,m.boxes[0]/m.boxes[1]];
 if(m.type==='bounty'||m.type==='combat')return [`${m.kills} kill${m.kills===1?'':'s'}`,null];
 const i=m.obj.findIndex(o=>o[1]===1);return [`step ${i<0?m.obj.length:i+1}/${m.obj.length}`,(i<0?m.obj.length:i)/m.obj.length]}
const objHtml=o=>`<div class="obj ${o[1]===2?'done':o[1]===1?'cur':''} ${o[3]?'opt':''}"><span class="box"></span>${o[3]?'<span class="optl">OPTIONAL</span> ':''}${esc(o[0])}${o[2]?`<span class="tag" style="margin-left:auto">${o[2]}</span>`:''}</div>`;
/* the type-specific block: only what the log gives for that type */
function progHtml(m){if(m.live)return '';
 if(m.drops){const by={};m.drops.forEach(x=>(by[x[3]]=by[x[3]]||[]).push(x));
  return `<div class="mprog"><div class="mph">CARGO · from the log's delivery lines</div>`+Object.entries(by).map(([to,xs])=>`<div class="mdest">${ic('i-obj','ic sm')}${esc(to)}</div>`+xs.map(([c,a,b])=>`<div class="mdrop ${a>=b?'done':''}"><span>${esc(c)}</span><span class="mbar"><i style="width:${Math.round(100*a/b)}%"></i></span><b>${a}/${b} SCU</b></div>`).join('')).join('')+`</div>`}
 if(m.type==='delivery')return `<div class="mprog"><div class="mph">PACKAGES</div><div class="mdrop"><span>Delivered</span><span class="mbar"><i style="width:${Math.round(100*m.boxes[0]/m.boxes[1])}%"></i></span><b>${m.boxes[0]}/${m.boxes[1]}</b></div></div>`;
 if(m.type==='bounty'||m.type==='combat')return `<div class="mprog"><div class="mph">FIGHT</div><div class="mstat"><b>${m.kills}</b><span>ships you destroyed since accepting</span></div>${m.waves?`<div class="mstat"><b>${m.waves}</b><span>wave notices</span></div>`:''}<div class="unit">The game doesn't log how many are left.</div></div>`;
 if(m.type==='fps')return `<div class="mprog"><div class="mph">GROUND</div><div class="mstat"><b>${m.kills}</b><span>enemies you killed on foot since accepting</span></div></div>`;
 return ''}
function chips(m){const T=MTYPE[m.type];return `<span class="mtype">${ic(T[1],'ic sm')}${T[0]}</span>${m.diff?`<span class="mchip">${m.diff}</span>`:''}${m.rank?`<span class="mchip">${esc(m.rank)}</span>`:''}${m.rep?`<span class="mchip rep">+${m.rep} rep</span>`:''}${m.bp?'<span class="mchip bp">BLUEPRINT</span>':''}`}
function msnRender(){
 const m=PM(), card=$('.msncard');
 if(!m){if(card)card.innerHTML='<div class="ph">PINNED</div><div class="unit">No contracts yet</div>';
  const pp=$('#msnPinned');if(pp)pp.innerHTML='<div class="ph">PINNED</div><div class="unit">No contracts yet</div>';
  const ml=$('#msnList');if(ml)ml.innerHTML='<div class="ph">ACTIVE CONTRACTS · 0</div><div class="row unit">No contracts yet</div>';
  const mh=$('#msnHist');if(mh)mh.innerHTML='<div class="ph">THIS SESSION</div>'+(MHIST.length?'':'<div class="row unit">No contracts finished yet</div>');
  if(!MHIST.length)return}
 const d=m?mDist(m):null, met=m?mMetric(m):null;
 if(m){
 if(card){
  const done=m.obj.filter(o=>o[1]===2).length, open=m.obj.filter(o=>o[1]!==2).slice(0,2);
  card.innerHTML=`<div class="ph">PINNED · ${MTYPE[m.type][0]}<span class="r">${done?done+' done · ':''}<button class="mapbtn" id="pinNext">${m.n} of ${MISSIONS.length} ▸</button></span></div>
   <div class="mgrid"><div><div class="title">${esc(m.title)}</div>${open.map(objHtml).join('')}</div>
   <div class="mside">${m.live?'':`<div><div class="unit">DISTANCE</div><b>${d!=null?d+' Gm':m.place?SYSN[m.place[0]]:'—'}</b></div><div><div class="unit">PROGRESS</div><b>${met[0]}</b></div>`}<div><div class="unit">TIME ON IT</div><b>${m.time}</b></div></div></div>`;
  $('#pinNext').onclick=e=>{e.stopPropagation();pin(pinned%MISSIONS.length+1)};
 }
 const pp=$('#msnPinned'); if(pp)pp.innerHTML=`<div class="ph">PINNED<span class="r">${m.shared?'shared by '+esc(m.shared)+' · ':''}${m.time}</span></div>
   <div style="font-size:20px;color:var(--hi);margin-bottom:4px">${esc(m.title)}</div>
   <div class="mchips">${chips(m)}</div>
   <div class="unit" style="margin:4px 0 10px">${m.live?(m.time==='now'?'accepted just now':'accepted '+m.time+' ago'):esc(m.giver)+' · '+esc(mWhere(m))}</div>
   <div class="mdet"><div>${m.obj.map(objHtml).join('')}</div>${progHtml(m)}</div>
   `;
 const ml=$('#msnList'); if(ml){ml.innerHTML=`<div class="ph">ACTIVE CONTRACTS · ${MISSIONS.length}<span class="r">tap to pin · in-game tracking isn't logged</span></div>`+
   MISSIONS.map(x=>{const mt=mMetric(x);return `<button class="mrow ${x.n===pinned?'on':''}" data-pin="${x.n}"><span class="mnum">${x.n}</span><span class="mtx"><b>${ic(MTYPE[x.type][1],'ic sm')} ${esc(x.title)}</b><span>${esc(curObj(x)[0])}</span></span><span class="mmet">${mt[1]!=null?`<span class="mbar"><i style="width:${Math.round(100*mt[1])}%"></i></span>`:''}${mt[0]}</span><span class="mwh">${esc(mWhere(x))}</span><span class="mpinb">${x.n===pinned?'PINNED':'PIN'}</span></button>`}).join('');}
 }
 const mh=$('#msnHist'); if(mh)mh.innerHTML=`<div class="ph">THIS SESSION</div>`+
   MHIST.map(([t,ty,res,pay,rep])=>`<div class="row"><span class="dot ${res==='Complete'?'':res==='Failed'?'r':'m'}"></span>${ic(MTYPE[ty][1],'ic sm')} ${esc(t)}<span class="r">${res==='Complete'?(pay?'+'+pay.toLocaleString()+' aUEC':'complete'):res.toLowerCase()}${rep?` · ${rep>0?'+':''}${rep} rep`:''}</span></div>`).join('');
}
document.addEventListener('click',e=>{const p=e.target.closest('[data-pin]');if(p&&!p.closest('.zclone')){e.stopPropagation();pin(+p.dataset.pin)}},true);

let pinMap=false;
function damage(){
 const el=$('#dmgp'), mp=$('.mapp'); if(!el)return;
 const fight=false;
 mp.classList.toggle('fight',fight);
 const hdr=mp.querySelector('.ph');
 if(!hdr.dataset.orig)hdr.dataset.orig=hdr.innerHTML;
 if(fight){hdr.innerHTML=`<span style="color:#ff8a94">COMBAT · DAMAGE</span><span class="r"><button class="mapbtn" id="mapPin">MAP</button></span>`;$('#mapPin').onclick=()=>{pinMap=true;render()}}
 else{hdr.innerHTML=hdr.dataset.orig+(combat&&pinMap?'':'');
  if(combat&&pinMap){const r=hdr.querySelector('.r');r.innerHTML='<button class="mapbtn" id="dmgBack">DAMAGE</button>';$('#dmgBack').onclick=()=>{pinMap=false;render()}}
  hdr.querySelectorAll('.mapmodes button').forEach(b=>b.onclick=()=>{hdr.querySelectorAll('.mapmodes button').forEach(x=>x.classList.toggle('on',x===b));$('#mapSys').style.display=b.dataset.z==='sys'?'':'none';$('#mapLoc').style.display=b.dataset.z==='loc'?'':'none'})}
 el.innerHTML='';
}
let render=function(){applyShip();ann();cviewSync();document.body.classList.toggle('bigship',bigShip);threat();ann();damage();msnRender();drawMap();defense();pips();deckRender();needs_();feed_();rings();sys();fig();gunList();o2();loadout();phoneRender();bodyCard();lastShipSil();suitHud();trimDist();fitThreat();placeTicker();};



/* ---- live: poll CSR, build the model, draw ---- */
let MODEL=slateModel({}), LASTSHIP={t:0}, SHIPREQ='', SHIPFAIL={};
const KEY=new URLSearchParams(location.search).get('k');
function api(p){const u=new URL(p,location.href);if(KEY)u.searchParams.set('k',KEY);return u}
function shipNameOf(c){const x=ALLSHIPS.find(a=>a.class===c)||(SHIPS[c]&&SHIPS[c].caps);return x&&x.name?x.name:String(c||'').replace(/_\d+$/,'').replace(/^[A-Z]{3,4}_/,'').replace(/_/g,' ')}
function killRows(n){return(((MODEL.log||{}).kills)||[]).slice(-n).reverse()}
function mtypeOf(n){n=String(n||'');return /bounty/i.test(n)?'bounty':/haul|cargo/i.test(n)?'haul':/deliver/i.test(n)?'delivery':/mining|mine /i.test(n)?'mining':/salvage|recover/i.test(n)?'recover':/eliminate|defend|kill|patrol/i.test(n)?'combat':'contract'}
let PLACES=[],MAPSET=false,O2H=[],CMMAX={decoy:0,noise:0};
function zoneLabel(){const G=MODEL.log||{};return G.armistice===true?'Armistice':G.jurisdiction?String(G.jurisdiction):G.armistice===false?'No armistice':''}
/* minutes of suit O2 left, from the drop over the last 90 s on EVA; null until it is measurable */
function o2Mins(){if(O2H.length<2)return null;const a=O2H[0],b=O2H[O2H.length-1],drop=a[1]-b[1],min=(b[0]-a[0])/60000;
 if(drop<=0.2||min<0.25)return null;return Math.max(0,Math.round(b[1]/(drop/min)))}
const T0=Date.now();let WAL0=null,FLOWN=0,LASTT=0;const SESS={downed:0,dead:0,respawn:0,seat:0},SEEN=new Set();
/* the recorder keeps the last 50 events: count each one once, by time and type */
function countEvents(evs){(evs||[]).forEach(e=>{const k=e.t+'|'+e.type;if(SEEN.has(k))return;SEEN.add(k);const t=String(e.type||'');
 if(t.indexOf('DOWNED')>=0)SESS.downed++;else if(t.indexOf('RESPAWN')>=0)SESS.respawn++;else if(t.indexOf('DEAD')>=0)SESS.dead++;else if(t.indexOf('ENTER_SEAT')>=0)SESS.seat++})}
function phDmgRows(tot,hull){const r=[];if(tot!=null)r.push([tot<30?'maj':'min','SHIELDS',tot+'%',tot]);if(hull!=null)r.push([hull<35?'maj':'min','HULL',hull+'%',hull]);
 Object.entries(MODEL.ann||{}).forEach(([k,l])=>{if(l==='maj'||l==='min')r.push([l,k.toUpperCase(),l==='maj'?'fault':'warning',null])});return r}
function agoMin(t){const m=Math.max(0,Math.round((Date.now()/1000-t)/60));return m<1?'now':m+' min'}
function registerShip(p){const c=Object.assign({},p,{pilotGunList:p.pilotGuns||[]});
 SHIPS[p.class]=fromCaps(c);if(p.shape)SHAPE[p.class]=p.shape;if(p.dims)SDIM[p.class]=p.dims;if(p.speeds)SPEEDS[p.class]=p.speeds;
 if(!ALLSHIPS.some(x=>x.class===p.class))ALLSHIPS.push(c)}
/* one request per class, kept for the page's life; a class asked under another spelling is aliased */
const SHIPPEND=new Set();
async function loadShip(cls){if(!cls||SHIPS[cls]||SHIPPEND.has(cls)||SHIPFAIL[cls])return;SHIPPEND.add(cls);
 try{const r=await fetch(api('/api/telemetry/ship?c='+encodeURIComponent(cls)),{credentials:'same-origin'});
  if(r.ok){const p=await r.json();registerShip(p);
   if(p.class!==cls){SHIPS[cls]=SHIPS[p.class];if(p.shape)SHAPE[cls]=p.shape;if(p.dims)SDIM[cls]=p.dims;if(p.speeds)SPEEDS[cls]=p.speeds}
   render()}else SHIPFAIL[cls]=1}catch(e){}finally{SHIPPEND.delete(cls)}}
const GICON={alert:'i-skull',msn:'i-obj',money:'i-pay',people:'i-party',ship:'i-land',zone:'i-zone',tip:'i-bell'};
function applyModel(){const M=MODEL,f=M.flight;
 const s=M.seat==='crew'?'gunner':(M.seat||'pilot');
 if(s!==seat){if((seat==='pilot'||seat==='gunner')&&LASTSHIP.seen)LASTSHIP.t=Date.now();lastSeat=seat;seat=s}
 foot=seat==='foot'||seat==='eva';
 combat=!!M.threat;
 if(M.ship&&M.ship.cls){shipK=M.ship.cls;loadShip(shipK)}
 SNOW=Math.round(f.speed||0);GNOW=f.g;if(f.heading!=null)HDG=f.heading;
 CM={decoy:M.cm.decoy,noise:M.cm.noise};FIREGROUP=M.preset||'';WSET={gimbals:!!M.gimbalsLocked,stagger:!!M.staggered};
 /* null = the game didn't send it: the key shows — */
 const dp=v=>v==null?null:/DEPLOY/.test(v)&&!/RETRACT/.test(v), sv=v=>v==null?null:!!v, sp=M.ship||{};
 STATE=Object.assign(STATE,{master:f.master,gear:dp(f.gear),vtol:dp(f.vtol),wings:dp(f.wings),
  lights:sv(sp.lights),doors:sv(sp.doorsOpen),locks:sv(sp.doorsLocked),
  coupled:M.ifcs.ifcs_coupled?!!M.ifcs.ifcs_coupled.active:null,power:(M.power||{}).powerplants==null?null:M.power.powerplants!==false});
 const md=$('#mode');if(md)md.textContent=f.master?f.master.toUpperCase():'—';
 const P=M.power||{};PGON={pw:P.powerplants!==false,en:P.engines!==false,sh:P.shield_generators!==false,wp:P.weapons!==false};
 hrNow=Math.round(M.vitals.hr||0);if(M.env.temp!=null)outT=Math.round(M.env.temp);
 const G=M.log||{};
 feed=(G.notices||[]).slice().reverse().map(n=>[new Date(n.t*1000).toTimeString().slice(0,5),GICON[n.group]||'i-bell',n.group,n.text,false,'','']);
 if(G.place){const sy=Object.keys(STARMAP).find(s=>STARMAP[s].some(r=>r[0]===G.place));
  if(sy){HERE.sys=sy;if(!MAPSET){mapView=sy;MAPSET=true}}HERE.name=G.place;
  if(!PLACES.length||PLACES[0].n!==G.place){PLACES.unshift({n:G.place,t:Date.now()});PLACES.length=Math.min(PLACES.length,8)}}
 const ns=$('#navStops');if(ns)ns.innerHTML=PLACES.length?PLACES.map((p,i)=>`<div class="row">${esc(p.n)}<span class="r">${i?agoMin(p.t/1000)+' ago':'here'}</span></div>`).join(''):'<div class="row unit">No places yet</div>';
 MHIST=(G.contracts||[]).filter(c=>c.status!=='active').reverse().map(c=>[c.name,mtypeOf(c.name),c.status==='complete'?'Complete':'Failed',0,0]);
 MISSIONS=(G.contracts||[]).filter(c=>c.status==='active').map((c,i)=>({n:i+1,live:true,type:mtypeOf(c.name),title:c.name,
  obj:[[c.objective||'No objective logged',1,'',false]],place:null,placeName:'',giver:'',party:'—',time:agoMin(c.t),kills:0}));
 const sx=(id,v)=>{const e=$('#'+id);if(e)e.textContent=v};
 sx('navPlace',G.place||'—');sx('mapPlace',G.place||'');sx('h2t',f.h2==null?'—':Math.round(f.h2)+'%');
 const hb=$('#h2');if(hb)hb.style.width=(f.h2||0)+'%';sx('navJur',G.jurisdiction||'—');sx('navZone',G.armistice===true?'Armistice':G.armistice===false?'Outside armistice':'—');
 /* last ship: the values seen while seated, kept once you leave the seat */
 if(M.ship){const SFl=M.shields||{};Object.assign(LASTSHIP,{seen:true,hull:M.hull,sh:SFl.bubble!=null?Math.round(SFl.bubble):SFl.faces?Math.round(SFl.faces.reduce((a,b)=>a+b,0)/SFl.faces.length):null,h2:f.h2==null?null:Math.round(f.h2),qt:f.qt==null?null:Math.round(f.qt)})}
 const pctv=v=>v==null?'—':Math.round(v)+'%',bar=(id,p)=>{const e=$('#'+id);if(e)e.style.width=(p==null?0:Math.max(0,Math.min(100,p)))+'%'};
 sx('qtt',pctv(f.qt));bar('qtb',f.qt);const br=(f.boost||{}).reserves;sx('bst',pctv(br));bar('bstb',br);
 ['decoy','noise'].forEach(k=>{if(M.cm[k]!=null)CMMAX[k]=Math.max(CMMAX[k],M.cm[k])});
 sx('gDec',M.cm.decoy==null?'—':String(M.cm.decoy));bar('gDecb',M.cm.decoy==null||!CMMAX.decoy?null:100*M.cm.decoy/CMMAX.decoy);
 sx('gNoi',M.cm.noise==null?'—':String(M.cm.noise));bar('gNoib',M.cm.noise==null||!CMMAX.noise?null:100*M.cm.noise/CMMAX.noise);
 const vo=M.vitals.o2;if(seat==='eva'&&vo!=null){O2H.push([Date.now(),vo]);while(O2H.length&&Date.now()-O2H[0][0]>90000)O2H.shift()}else O2H=[];
 sx('o2v',pctv(vo));const om=o2Mins();sx('o2t',om==null?'—':om+' min');
 sx('o2env',[seat==='eva'?'EVA':'',M.env.breathable===false?'no air':M.env.breathable?'air':'',M.vitals.gravity!=null?M.vitals.gravity.toFixed(1)+' g':''].filter(Boolean).join(' · '));
 const pc=v=>v==null?'—':v+'%';sx('lsHull',pc(LASTSHIP.hull));sx('lsSh',pc(LASTSHIP.sh));sx('lsH2',pc(LASTSHIP.h2));sx('lsQt',pc(LASTSHIP.qt));
 /* this session: since the page opened */
 const now=Date.now();if(M.live&&M.ship&&LASTT)FLOWN+=(f.speed||0)*Math.min(5,(now-LASTT)/1000);LASTT=now;
 if(WAL0==null&&M.wallet!=null)WAL0=M.wallet;
 const dW=M.wallet!=null&&WAL0!=null?Math.round(M.wallet-WAL0):null,big=n=>n>=1e6?(n/1e6).toFixed(1)+' M':n.toLocaleString('en-US');
 sx('sessT','since '+new Date(T0).toTimeString().slice(0,5));
 sx('sWal',dW==null?'—':(dW>0?'+':dW<0?'−':'')+Math.abs(dW).toLocaleString('en-US'));sx('sBal',M.wallet==null?'—':big(Math.round(M.wallet)));
 sx('sFlown',FLOWN>=1000?(FLOWN/1000).toFixed(1)+' km':Math.round(FLOWN)+' m');sx('sBp',String(G.blueprints||0));
 sx('sDown',String(SESS.downed));sx('sDead',String(SESS.dead));sx('sResp',String(SESS.respawn));sx('sSeat',String(SESS.seat));
 const nr=$('#mainNear');if(nr){const k=killRows(2);nr.innerHTML=k.length?k.map(x=>`<div class="line unit">${esc(x.killer||'Unknown')} destroyed a ${esc(shipNameOf(x.cls))} · ${agoMin(x.t)}</div>`).join(''):'<div class="line unit">No ships destroyed nearby</div>'}
}
const GUNHOLD={};
function applyWeapons(){if(!(MODEL.weapons&&MODEL.weapons.length))return;const now=Date.now();
 weapons=MODEL.weapons.map((w,i)=>{w=w.slice();if(w[2]&&!w[3])GUNHOLD[i]=now;else if(w[2]&&GUNHOLD[i]&&now-GUNHOLD[i]<1500)w[3]=false;return w})}
/* hits: the HIT light flashes while shots land. 4.10 sends no hull or shield numbers. */
const HITS={n:0,t:0,on:false};
function countHits(){const on=MODEL.ann&&MODEL.ann.hit==='maj';if(on&&!HITS.on){HITS.n++;HITS.t=Date.now()}HITS.on=on;if(!MODEL.ship){HITS.n=0;HITS.on=false}}
let LASTN=0,LASTR=0,LASTK='',LASTSEQ=0,SEQOK=false;
/* 4 polls a second while live: instruments every poll, the full redraw once a second or when the scene changes */
async function poll(){let wait=2000,p=null;
 try{const r=await fetch(api('/api/telemetry/live'),{credentials:'same-origin',cache:'no-store'});if(r.ok)p=await r.json()}catch(e){}
 try{MODEL=slateModel(p||{},SHIPS[shipK]?SH().caps:null);MODEL.names=(p&&p.names)||{};if(p)countEvents(p.events);
  const G=MODEL.log||{},n=(G.notices||[]).length;
  applyModel();applyWeapons();countHits();
  /* the log keeps the last 40 notices; seq counts every one, so new ones are seq - LASTSEQ (first poll is history) */
  const sq=G.seq||0;if(p&&p.log){if(SEQOK&&sq>LASTSEQ)G.notices.slice(-Math.min(3,sq-LASTSEQ,n)).forEach(x=>{unread++;toast(GICON[x.group]||'i-bell',x.group,x.text)});LASTSEQ=sq;SEQOK=true}
  wait=MODEL.live?250:2000;
  const wp=$('#waitPanel');if(wp){wp.hidden=MODEL.live;wp.querySelector('b').textContent=MODEL.wait}
  const k=[MODEL.live,seat,shipK,combat,!!SHIPS[shipK],n,(G.contracts||[]).length,(G.kills||[]).length,G.place].join('|');
  if(k!==LASTK||Date.now()-LASTR>=1000){LASTK=k;LASTR=Date.now();render()}else if(MODEL.live)liveTick()}catch(e){console.error(e)}
 setTimeout(poll,wait)}
const _render=render;render=function(){_render();applyWeapons()};
addEventListener('resize',()=>{layout();render()});
layout();render();poll();
"""

def slate_html():
    """The whole page. /*__FONTS__*/ is filled in by sc_stats.LIVE_HTML."""
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
            '<meta name="apple-mobile-web-app-capable" content="yes"><meta name="mobile-web-app-capable" content="yes">'
            '<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">'
            '<meta name="apple-mobile-web-app-title" content="CSR Live"><meta name="theme-color" content="#050d12">'
            '<title>Kestrel K-7 · CSR Live</title><style>/*__FONTS__*/' + SLATE_CSS + '</style></head><body>'
            + SLATE_BODY + '<script>' + SLATE_MODEL_JS + SLATE_JS + '</script></body></html>')
