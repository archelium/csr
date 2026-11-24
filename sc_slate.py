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
                    injuries: {}, loadout: {}, blueprints: 0};
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
             master: mm.indexOf('NAV') === 0 ? 'nav' : mm === 'SCM' ? 'scm' : mm === 'STEALTH' ? 'stealth' : null,
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
