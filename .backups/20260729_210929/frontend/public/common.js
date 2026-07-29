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
  const lines = [
    `⭐ ${name}${status || ''}`,
    `修为：${basic.cultivation || '未知'}`,
    `身份：${basic.identity || '散修'}`,
    `年龄：${basic.age || '?'}岁  身高：${basic.height || '?'}  ${basic.race || ''} ${basic.gender || ''}`,
    basic.faction ? `势力：${basic.faction} - ${basic.position || ''}` : '',
    bodyLines.length ? bodyLines.join(' | ') : '',
    voiceParts.length ? `声音：${voiceParts.join('，')}` : '',
    persLines.length ? `\n【性格】\n${persLines.join('\n')}` : '',
    cultLines.length ? `\n【修炼】\n${cultLines.join('\n')}` : '',
    weapons.length ? `\n装备：${weapons.map(w => w.name).join('、')}` : '',
    bg.history ? `\n【身世】\n${bg.history.substring(0, 300)}` : '',
    relParts.length ? `\n【关系】\n${relParts.join(' | ')}` : '',
  ].filter(Boolean).join('\n');
  return lines;
}
