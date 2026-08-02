// ── Shared utility functions for ANE frontend pages ──

// ── Frontend logger ──
function frontendLog(level, message) {
  console.log(`[${level}] ${message}`);
  var payload = {level: level, message: message, source: 'browser'};
  var uid = '';
  try {
    var t = getToken();
    if (t) {
      var parts = t.split('.');
      if (parts.length === 3) {
        var data = JSON.parse(atob(parts[1]));
        if (data.sub) uid = data.sub;
        payload.user_id = uid;
      }
    }
  } catch(e) {}
  fetch('/api/log', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload),
  }).catch(() => {});
}

// ── Auth — JWT token management ──
function getToken() { return localStorage.getItem('ane_token'); }
function setToken(t) { localStorage.setItem('ane_token', t); }
function clearToken() { localStorage.removeItem('ane_token'); localStorage.removeItem('ane_user'); }

function isLoggedIn() { return !!getToken(); }

/** Wrapped fetch that auto-injects auth header and handles 401. */
async function apiFetch(url, opts = {}) {
  const token = getToken();
  const headers = opts.headers || {};
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }
  opts.headers = headers;
  opts.credentials = 'same-origin';
  const res = await fetch(url, opts);
  if (res.status === 401 && token) {
    clearToken();
    location.href = '/login';
    throw new Error('Unauthorized');
  }
  return res;
}

// ── HTML escaping ──
function escHtml(s) {
  if (!s) return '';
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function escAttr(s) { return (s||'').replace(/"/g, '&quot;').replace(/</g,'&lt;'); }

// ── Color settings helpers (shared across chat.html and settings.html) ──
function applyFont(value) {
  const fontMap = {
    default: '"Microsoft YaHei", "SimSun", serif',
    songti: '"SimSun", "Songti SC", serif',
    heiti: '"SimHei", "Heiti SC", sans-serif',
    kaiti: '"KaiTi", "STKaiti", serif',
    fangsong: '"FangSong", "STFangsong", serif',
    lishu: '"LiSu", serif',
    xingkai: '"STXingkai", "Xingkai SC", fantasy',
  };
  const family = fontMap[value] || fontMap.default;
  document.body.style.fontFamily = family;
  localStorage.setItem('font_family', value);
}

function applyColor(type, hex) {
  hex = hex.trim();
  if (!/^#[0-9a-fA-F]{6}$/.test(hex)) return;
  document.getElementById('color-picker-' + type).value = hex;
  document.getElementById('color-text-' + type).value = hex;
  document.documentElement.style.setProperty('--color-' + type, hex);
  localStorage.setItem('color_' + type, hex);
}

function rgbToHex(rgb) {
  const m = rgb.match(/\d+/g);
  if (!m) return '#d4c8b8';
  return '#' + m.slice(0,3).map(x => parseInt(x).toString(16).padStart(2,'0')).join('');
}

function resetColors() {
  const defaults = {'ai':'#d4c8b8','system':'#6b5a3e','user':'#e8dcc8'};
  ['ai','system','user'].forEach(t => {
    document.documentElement.style.setProperty('--color-' + t, defaults[t]);
    localStorage.setItem('color_' + t, defaults[t]);
    var picker = document.getElementById('color-picker-' + t);
    var text = document.getElementById('color-text-' + t);
    if (picker) picker.value = defaults[t];
    if (text) text.value = defaults[t];
  });
}

// ── Session avatar (per-world) ──
function getSessionAvatar(sid) {
  if (!sid) return '';
  try { return localStorage.getItem('ane_avatar_' + sid) || ''; } catch(e) { return ''; }
}
function setSessionAvatar(sid, dataUrl) {
  if (!sid) return;
  try { localStorage.setItem('ane_avatar_' + sid, dataUrl); } catch(e) {}
}

// ── System avatar (per-world, for AI messages) ──
function getSystemAvatar(sid) {
  if (!sid) return '';
  try { return localStorage.getItem('ane_sys_avatar_' + sid) || ''; } catch(e) { return ''; }
}
function setSystemAvatar(sid, dataUrl) {
  if (!sid) return;
  try { localStorage.setItem('ane_sys_avatar_' + sid, dataUrl); } catch(e) {}
}

// ── NPC model formatting (shared across home.html and chat.html) ──
// SAFE: returns plain text, all user data already runs through escHtml.
// Callers MUST use textContent (not innerHTML) or escHtml() each field before HTML insertion.
function formatNpcModel(md, name, status) {
  if (!md) return '';
  const basic = md.basic || {};
  const appearance = md.appearance || {};
  const face = appearance.face || {};
  const skin = appearance.skin || {};
  const hair = appearance.hair || {};
  const bodyLines = [];
  if (appearance.overall_impression) bodyLines.push(`整体印象：${appearance.overall_impression}`);
  if (face.eyes) bodyLines.push(`眼眸：${face.eyes}`);
  if (face.lips) bodyLines.push(`嘴唇：${face.lips}`);
  if (face.shape) bodyLines.push(`脸型：${face.shape}`);
  if (skin.color) bodyLines.push(`肤色：${skin.color}`);
  if (hair.style && hair.color && hair.length) bodyLines.push(`头发：${hair.length} ${hair.style}，${hair.color}`);
  const cloth = md.clothing || {};
  const clothParts = [];
  if (cloth.type) clothParts.push(cloth.type);
  if (cloth.color) clothParts.push(cloth.color);
  if (cloth.material) clothParts.push(cloth.material);
  if (clothParts.length) bodyLines.push(`衣着：${clothParts.join('，')}`);
  const pers = md.personality || {};
  const persLines = [];
  if (pers.core) persLines.push(`核心：${pers.core}`);
  if (pers.obsession) persLines.push(`执念：${pers.obsession}`);
  if (pers.values) persLines.push(`价值观：${pers.values}`);
  const cult = md.cultivation || {};
  const cultLines = [];
  if (cult.spiritual_root) cultLines.push(`灵根：${cult.spiritual_root}`);
  if (cult.techniques) cultLines.push(`功法：${cult.techniques}`);
  const voice = md.voice || {};
  const voiceParts = [];
  if (voice.timbre) voiceParts.push(voice.timbre);
  if (voice.speed) voiceParts.push(voice.speed);
  const weapons = (md.equipment || []).filter(e => e.name);
  const bg = md.background || {};
  const relationships = md.relationships || {};
  const relParts = [];
  if (relationships.master) relParts.push(`师尊：${relationships.master}`);
  if (relationships.father) relParts.push(`父：${relationships.father}`);
  if (relationships.mother) relParts.push(`母：${relationships.mother}`);
  if (relationships.lover) relParts.push(`恋人：${relationships.lover}`);
  // ── Generic extra sections ──
  // Worldview-generic: any top-level section not covered above (magic,
  // knighthood, lifestyle, …) is rendered recursively so per-world schemas
  // don't lose data in the detail view.
  const LABEL = {
    basic:'基础', appearance:'外貌', face:'脸部', skin:'皮肤', hair:'头发',
    voice:'声音', attire:'穿着', clothing:'衣着', jewelry:'首饰', equipment:'装备',
    behavior:'行为', speech_style:'说话', combat_style:'出手', personality:'性格',
    background:'身世', cultivation:'修炼', knowledge_bounds:'信息边界',
    attitude_to_player:'对玩家的态度', relationships:'关系', nsfw:'身体',
    overall_impression:'整体印象', body_proportion:'体型', aura:'气质', eyes:'眼眸',
    lips:'嘴唇', shape:'脸型', color:'颜色', style:'款式', length:'长度', ornament:'装饰',
    timbre:'音色', speed:'语速', volume:'音量', name:'姓名', race:'种族', gender:'性别',
    age:'年龄', height:'身高', cultivation:'修为', identity:'身份', faction:'势力',
    position:'职位', core:'核心', values:'价值观', fears:'恐惧', likes:'喜好',
    obsession:'执念', history:'身世', major_events:'重大事件', family:'家族',
    spiritual_root:'灵根', techniques:'功法', divine_powers:'神通', ring_storage:'储物',
    wealth:'财富', knows:'知道', does_not_know:'不知道', suspicious_of:'怀疑',
    surface:'表面', true_feelings:'真实想法', relationship_trend:'关系趋势',
    master:'师尊', father:'父', mother:'母', lover:'恋人', friends:'朋友', enemies:'敌人',
    description:'描述', position:'位置'
  };
  const COVERED = {basic:1, appearance:1, voice:1, equipment:1, personality:1, background:1, cultivation:1, relationships:1, clothing:1, nsfw:1};
  function flatLabel(k) { return LABEL[k] || k; }
  function fmtValue(v) {
    if (v === true) return '是';
    if (v === false) return '否';
    if (v === null || v === undefined) return '';
    return String(v);
  }
  function renderSection(obj, depth, out) {
    for (const k in obj) {
      if (k === 'model_version') continue;
      const v = obj[k];
      if (v === null || v === undefined || v === '') continue;
      if (Array.isArray(v)) {
        const items = v.filter(x => x !== '' && x !== null && x !== undefined);
        if (!items.length) continue;
        out.push(`${'  '.repeat(depth)}${flatLabel(k)}：`);
        items.slice(0, 8).forEach(it => {
          if (typeof it === 'object' && it !== null) {
            const sub = [];
            renderSection(it, depth + 1, sub);
            out.push(...sub);
          } else {
            out.push(`${'  '.repeat(depth + 1)}· ${fmtValue(it)}`);
          }
        });
      } else if (typeof v === 'object' && v !== null) {
        if (Object.values(v).every(x => x === '' || x === null || x === undefined || (Array.isArray(x) && !x.length))) continue;
        out.push(`${'  '.repeat(depth)}${flatLabel(k)}：`);
        renderSection(v, depth + 1, out);
      } else {
        out.push(`${'  '.repeat(depth)}${flatLabel(k)}：${fmtValue(v)}`);
      }
    }
  }
  const extraLines = [];
  for (const sec in md) {
    if (sec === 'model_version' || COVERED[sec]) continue;
    if (typeof md[sec] !== 'object' || md[sec] === null) continue;
    if (Object.values(md[sec]).every(x => x === '' || x === null || x === undefined || (Array.isArray(x) && !x.length))) continue;
    extraLines.push(`\n【${flatLabel(sec)}】`);
    renderSection(md[sec], 1, extraLines);
  }
  const lines = [
    `⭐ ${name}${status || ''}`,
    `修为：${basic.cultivation || basic.level || basic.rank || '未知'}`,
    `身份：${basic.identity || basic.title || basic.occupation || '散修'}`,
    `年龄：${basic.age || '?'}岁  身高：${basic.height || '?'}  ${basic.race || ''} ${basic.gender || ''}`,
    basic.faction ? `势力：${basic.faction} - ${basic.position || ''}` : '',
    bodyLines.length ? bodyLines.join(' | ') : '',
    voiceParts.length ? `声音：${voiceParts.join('，')}` : '',
    persLines.length ? `\n【性格】\n${persLines.join('\n')}` : '',
    cultLines.length ? `\n【修炼】\n${cultLines.join('\n')}` : '',
    weapons.length ? `\n【装备】\n${weapons.map(w => w.name + (w.description ? '：' + w.description : '')).join('\n')}` : '',
    bg.history ? `\n【身世】\n${bg.history.substring(0, 300)}` : '',
    relParts.length ? `\n【关系】\n${relParts.join(' | ')}` : '',
  ].filter(Boolean).join('\n');
  return lines + (extraLines.length ? '\n' + extraLines.join('\n') : '');
}
