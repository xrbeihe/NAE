/* Shared worldview selection + character-creation helpers (ES5).
 *
 * Loaded by both chat.html and app.html. Centralizes the worldview
 * dropdown logic so new-worldview features are written once.
 *
 * The host page is expected to provide:
 *   - apiFetch(url, opts)           (common.js)
 *   - charTemplates                 (global, populated here)
 *   - applyCharacter(), updateCharHints(), selectGoldenFinger(), skipCharacter()
 *   - getElementById for #char-* elements
 *
 * Exposed globals:
 *   worldviewState  { list, currentId, currentName, ui }
 *   loadWorldviews()              → fetch GET /worldviews
 *   getCurrentWorldviewId()
 *   getCurrentWorldviewName()
 *   getWorldviewUi()              → ui.json for current worldview (cached)
 *   onWorldviewChange()           → reload templates + sects + toggle sections
 *   fillWorldviewSelect(selectEl) → populate the <select>
 */

var worldviewState = {
  list: [],           // [{id, name, description, tags, ...}]
  currentId: 'xianxia_v1',
  currentName: '修仙世界',
  ui: null,           // ui.json for current worldview
  loaded: false
};

function _wvFind(id) {
  for (var i = 0; i < worldviewState.list.length; i++) {
    if (worldviewState.list[i].id === id) return worldviewState.list[i];
  }
  return null;
}

function _wvPersist(id) {
  try { localStorage.setItem('ane_last_worldview', id); } catch (e) {}
}

function _wvRestore() {
  try { return localStorage.getItem('ane_last_worldview') || ''; } catch (e) { return ''; }
}

async function loadWorldviews(force) {
  try {
    var res = await apiFetch('/worldviews');
    if (!res.ok) return;
    var data = await res.json();
    worldviewState.list = (data.worldviews || []).map(function (w) {
      return { id: w.id, name: w.name, description: w.description || '' };
    });
    worldviewState.loaded = true;
    // Restore last selection if still available
    var last = _wvRestore();
    if (last && _wvFind(last)) worldviewState.currentId = last;
    var cur = _wvFind(worldviewState.currentId);
    if (cur) worldviewState.currentName = cur.name;
  } catch (e) { frontendLog('WARN', 'loadWorldviews failed: ' + e.message); }
}

function getCurrentWorldviewId() { return worldviewState.currentId; }
function getCurrentWorldviewName() { return worldviewState.currentName; }

function getWorldviewUi() {
  return worldviewState.ui || {};
}

function fillWorldviewSelect(selectEl) {
  if (!selectEl) return;
  if (!worldviewState.loaded) return;
  var opts = [];
  for (var i = 0; i < worldviewState.list.length; i++) {
    var w = worldviewState.list[i];
    var sel = (w.id === worldviewState.currentId) ? ' selected' : '';
    opts.push('<option value="' + escAttr(w.id) + '"' + sel + '>' + escHtml(w.name) + '</option>');
  }
  selectEl.innerHTML = opts.join('');
}

/* Reload character templates + sects for the currently selected worldview,
 * and toggle worldview-specific form sections (sect / golden-finger). */
async function onWorldviewChange() {
  var sel = document.getElementById('worldview-select');
  if (sel) {
    var id = sel.value || 'xianxia_v1';
    var w = _wvFind(id);
    worldviewState.currentId = id;
    worldviewState.currentName = w ? w.name : id;
    _wvPersist(id);
  }
  await reloadCharTemplatesForWorldview();
  if (typeof refreshCharModal === 'function') refreshCharModal();
  else if (typeof showCharacterModal === 'function') showCharacterModal(
    (document.getElementById('char-modal-overlay') || {}).dataset.sid || '__pending__'
  );
}

async function reloadCharTemplatesForWorldview() {
  var wv = getCurrentWorldviewId();
  var res = await apiFetch('/sessions/__any__/templates?worldview=' + encodeURIComponent(wv));
  if (res.ok) {
    charTemplates = await res.json();
    if (charTemplates) worldviewState.ui = charTemplates.ui || null;
  }
  // Sect list (only worldviews with sects show the section)
  var sectRow = document.getElementById('sect-row');
  var sectSel = document.getElementById('char-sect');
  try {
    var sres = await apiFetch('/sessions/models/sects?worldview=' + encodeURIComponent(wv));
    if (sres.ok) {
      var sdata = await sres.json();
      var sects = sdata.sects || [];
      if (sectSel) sectSel.innerHTML = '<option value="">' + (_wvSectPlaceholder() || '无') + '</option>' +
        sects.map(function (s) { return '<option value="' + escAttr(s) + '">' + escHtml(s) + '</option>'; }).join('');
      if (sectRow) sectRow.style.display = (sects.length > 0) ? '' : 'none';
    }
  } catch (e) { frontendLog('WARN', 'load sects failed: ' + e.message); }
  // Golden-finger section visibility
  var gfWrap = document.getElementById('gf-section');
  var hasGf = !!(charTemplates && charTemplates.golden_fingers && charTemplates.golden_fingers.length);
  if (gfWrap) gfWrap.style.display = hasGf ? '' : 'none';
  // Update create-button text from ui.json
  var btn = document.getElementById('char-submit-btn');
  if (btn && worldviewState.ui && worldviewState.ui.create_button) {
    btn.textContent = worldviewState.ui.create_button;
  }
  // Update dynamic field labels from ui.json (e.g. 修为 → 职业)
  var cultLabel = document.getElementById('char-cultivation-label');
  if (cultLabel) {
    var cultText = _wvLabel('cultivation', '修为');
    if (cultText) cultLabel.textContent = cultText;
  }
  // Update modal title from ui.json (fallback: 创建你的角色)
  var titleEl = document.getElementById('char-modal-title');
  if (titleEl) {
    var ui = getWorldviewUi();
    var t = ui && ui.modal_title;
    titleEl.textContent = t || '创建你的角色';
  }
}

function _wvSectPlaceholder() {
  var ui = getWorldviewUi();
  return (ui && ui.labels && ui.labels.sect_placeholder) || '无宗门（散修）';
}

/* Resolve a localized label from the worldview's ui.json, with a fallback. */
function _wvLabel(key, fallback) {
  var ui = getWorldviewUi();
  var v = ui && ui.labels && ui.labels[key];
  return (v && String(v).length) ? v : (fallback || '');
}

// ── Dynamic form rendering (form.json) ───────────────────────

var _inputStyle = 'width:100%;padding:8px 12px;background:var(--input-bg);border:1px solid var(--border);border-radius:4px;color:var(--text);font-family:inherit;font-size:14px;box-sizing:border-box;appearance:none;-webkit-appearance:none';
var _hintStyle = 'font-size:12px;color:var(--accent-dim);margin:2px 0 8px;line-height:1.5';
var _labelStyle = 'display:block;margin:10px 0 4px;font-size:13px;color:var(--text)';

function _escH(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function(c){ return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]; }); }

/* Check a visibility condition from form.json (has_sects / has_golden_fingers). */
function _visibleIf(field) {
  var cond = field.visible_if;
  if (!cond) return true;
  if (cond === 'has_sects') {
    var wt = charTemplates && charTemplates.world_templates;
    var sects = wt && wt.sects ? wt.sects : [];
    return sects.length > 0;
  }
  if (cond === 'has_golden_fingers') {
    return !!(charTemplates && charTemplates.golden_fingers && charTemplates.golden_fingers.length);
  }
  return true;
}

/* Render the character-creation fields into #char-fields per the form spec. */
function renderFormFields() {
  var container = document.getElementById('char-fields');
  if (!container) return;
  var form = charTemplates && charTemplates.form;
  if (!form || !form.fields) { renderLegacyFields(); return; }

  var html = '';
  form.fields.forEach(function (f) {
    if (!_visibleIf(f)) return;
    var key = f.key || '';
    var label = f.label || key;
    html += '<label style="' + _labelStyle + '">' + _escH(label) + '</label>';

    if (f.kind === 'text') {
      var randomBtn = f.random_button ? '<button onclick="randomName()" title="随机生成姓名" style="padding:8px 14px;background:var(--accent);color:#1a1410;border:none;border-radius:4px;cursor:pointer;font-size:16px;white-space:nowrap">🎲</button>' : '';
      html += '<div style="display:flex;gap:6px">' +
        '<input type="text" id="field-' + key + '" data-key="' + key + '" placeholder="' + _escH(f.placeholder || '') + '" value="' + _escH(f.default != null ? f.default : '') + '" maxlength="' + (f.maxlength || 20) + '" style="' + _inputStyle + ';flex:1">' +
        randomBtn + '</div>';
    } else if (f.kind === 'number') {
      html += '<input type="number" id="field-' + key + '" data-key="' + key + '" value="' + _escH(f.default != null ? f.default : '') + '" min="' + _escH(f.min != null ? f.min : '0') + '" max="' + _escH(f.max != null ? f.max : '999') + '" style="' + _inputStyle + '">';
    } else if (f.kind === 'select') {
      var opts = [];
      if (f.options_from === 'sects') {
        // populated later from /models/sects
        html += '<select id="field-' + key + '" data-key="' + key + '" data-options-from="sects" style="' + _inputStyle + '"></select>';
      } else {
        var src = f.options_from && charTemplates ? charTemplates[f.options_from] : null;
        if (src) {
          if (Array.isArray(src)) {
            src.forEach(function (o) {
              var v = o.value != null ? o.value : o.id;
              opts.push('<option value="' + _escH(v) + '">' + _escH(o.label || v) + (o.desc ? ' — ' + _escH(o.desc) : '') + '</option>');
            });
          } else if (typeof src === 'object') { // identities: {key: {...}}
            Object.keys(src).forEach(function (k) {
              opts.push('<option value="' + _escH(k) + '">' + _escH(src[k].label || k) + (src[k].desc ? ' — ' + _escH(src[k].desc) : '') + '</option>');
            });
          }
        }
        if (f.allow_custom) opts.push('<option value="__custom__">自定义</option>');
        html += '<select id="field-' + key + '" data-key="' + key + '" data-options-from="' + _escH(f.options_from || '') + '" data-hint-template="' + _escH(f.hint_template || '') + '" data-allow-custom="' + (f.allow_custom ? '1' : '') + '" data-derive="' + _escH((f.derive || []).join(',')) + '" style="' + _inputStyle + '">' + opts.join('') + '</select>';
      }
      html += '<div class="hint" id="hint-' + key + '" style="' + _hintStyle + '"></div>';
      if (f.allow_custom) {
        html += '<div id="custom-' + key + '-wrap" style="display:none;margin-top:6px">' +
          '<label style="' + _labelStyle + '">' + _escH(f.custom_label || '自定义') + '</label>' +
          '<textarea id="custom-' + key + '" data-key="' + key + '" placeholder="' + _escH(f.custom_label || '') + '…" maxlength="500" style="width:100%;padding:8px 12px;background:var(--input-bg);border:1px solid var(--border);border-radius:4px;color:var(--text);font-size:13px;font-family:inherit;resize:vertical;min-height:60px;box-sizing:border-box"></textarea></div>';
      }
    } else if (f.kind === 'card_grid') {
      html += '<div id="grid-' + key + '" data-key="' + key + '" data-options-from="' + _escH(f.options_from || '') + '" data-option-map="' + _escH(JSON.stringify(f.option_map || {})) + '" style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:6px;margin-top:8px"></div>';
      if (f.allow_custom) {
        html += '<div id="custom-' + key + '-wrap" style="display:none;margin-top:10px">' +
          '<label style="' + _labelStyle + '">' + _escH(f.custom_label || '自定义') + '</label>' +
          '<textarea id="custom-' + key + '" data-key="' + key + '" placeholder="' + _escH(f.custom_label || '') + '…" maxlength="500" style="width:100%;padding:10px 14px;background:var(--input-bg);border:1px solid var(--border);border-radius:4px;color:var(--text);font-size:13px;font-family:inherit;resize:vertical;min-height:80px;box-sizing:border-box"></textarea></div>';
      }
    }
  });

  container.innerHTML = html;
  bindFieldEvents();
  fillFormValues();
}

/* Legacy fallback: render the fixed xianxia-style field set (same DOM ids). */
function renderLegacyFields() {
  var container = document.getElementById('char-fields');
  if (!container) return;
  container.innerHTML =
    '<label style="' + _labelStyle + '">姓名</label>' +
    '<div style="display:flex;gap:6px"><input type="text" id="char-name" placeholder="输入你的道号或姓名" value="张二狗" maxlength="20" style="' + _inputStyle + ';flex:1"><button onclick="randomName()" title="随机生成姓名" style="padding:8px 14px;background:var(--accent);color:#1a1410;border:none;border-radius:4px;cursor:pointer;font-size:16px;white-space:nowrap">🎲</button></div>' +
    '<label style="' + _labelStyle + '">年龄</label><input type="number" id="char-age" value="19" min="12" max="999" style="' + _inputStyle + '">' +
    '<label style="' + _labelStyle + '">性别</label><select id="char-gender" style="' + _inputStyle + '"></select>' +
    '<label style="' + _labelStyle + '">出身背景</label><select id="char-background" style="' + _inputStyle + '"></select><div class="hint" id="char-background-hint" style="' + _hintStyle + '"></div>' +
    '<label style="' + _labelStyle + '" id="char-cultivation-label">' + _wvLabel('cultivation','修为') + '</label><select id="char-cultivation" style="' + _inputStyle + '"></select><div class="hint" id="char-cultivation-hint" style="' + _hintStyle + '"></div>' +
    '<label style="' + _labelStyle + '">性格</label><select id="char-personality" style="' + _inputStyle + '"></select><div class="hint" id="char-personality-hint" style="' + _hintStyle + '"></div>' +
    '<div id="personality-custom-wrap" style="display:none;margin-top:6px"><label style="' + _labelStyle + '">自定义性格描述</label><textarea id="personality-custom" placeholder="详细描写你的角色性格…" maxlength="500" style="width:100%;padding:8px 12px;background:var(--input-bg);border:1px solid var(--border);border-radius:4px;color:var(--text);font-size:13px;font-family:inherit;resize:vertical;min-height:60px;box-sizing:border-box"></textarea></div>' +
    '<label style="' + _labelStyle + '">身份</label><select id="char-identity" style="' + _inputStyle + '"></select><div class="hint" id="char-identity-hint" style="' + _hintStyle + '"></div>' +
    '<div id="identity-custom-wrap" style="display:none;margin-top:6px"><label style="' + _labelStyle + '">自定义身份描述</label><textarea id="identity-custom" placeholder="描述你的身份背景…" maxlength="300" style="width:100%;padding:8px 12px;background:var(--input-bg);border:1px solid var(--border);border-radius:4px;color:var(--text);font-size:13px;font-family:inherit;resize:vertical;min-height:60px;box-sizing:border-box"></textarea></div>' +
    '<div id="sect-row"><label style="' + _labelStyle + '">' + _wvLabel('sect','宗门') + '</label><select id="char-sect" style="' + _inputStyle + '"><option value="">无宗门（散修）</option></select><div class="hint" style="' + _hintStyle + '">选择你的初始宗门，系统会自动分配一个对应城市</div></div>' +
    '<hr style="border:none;border-top:1px solid var(--border);margin:16px 0">' +
    '<div id="gf-section"><label style="color:var(--accent);font-size:14px;margin-top:0">✨ 金手指 — 你的天命所在</label>' +
    '<div id="gf-grid" style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:6px;margin-top:8px"></div>' +
    '<div id="gf-custom-wrap" style="display:none;margin-top:10px"><label style="' + _labelStyle + '">自定义描写</label><textarea id="gf-custom" placeholder="详细描写你的金手指…" maxlength="500" style="width:100%;padding:10px 14px;background:var(--input-bg);border:1px solid var(--border);border-radius:4px;color:var(--text);font-size:13px;font-family:inherit;resize:vertical;min-height:80px;box-sizing:border-box"></textarea></div></div>';
  bindLegacyFieldEvents();
  fillLegacyValues();
}

/* Bind change handlers for dynamic form fields (hints + custom toggles). */
function bindFieldEvents() {
  var container = document.getElementById('char-fields');
  if (!container) return;
  container.querySelectorAll('select[data-options-from]').forEach(function (sel) {
    sel.addEventListener('change', function () { updateFormHint(sel); });
  });
}

function updateFormHint(sel) {
  var key = sel.dataset.key;
  var hintEl = document.getElementById('hint-' + key);
  if (!hintEl) return;
  var tpl = sel.dataset.hintTemplate;
  var customWrap = document.getElementById('custom-' + key + '-wrap');
  var isCustom = sel.value === '__custom__';
  if (customWrap) customWrap.style.display = isCustom ? 'block' : 'none';
  if (isCustom) { hintEl.textContent = '填写你的自定义描述'; return; }
  if (!tpl) { hintEl.textContent = ''; return; }
  var src = sel.dataset.optionsFrom && charTemplates ? charTemplates[sel.dataset.optionsFrom] : null;
  var opt = null;
  if (src) {
    if (Array.isArray(src)) opt = src.find(function (o) { return (o.value != null ? o.value : o.id) === sel.value; });
    else opt = src[sel.value];
  }
  if (!opt) { hintEl.textContent = ''; return; }
  hintEl.textContent = tpl.replace(/\{(\w+)\}/g, function (_, k) { return opt[k] != null ? opt[k] : ''; });
}

/* Fill dynamic select options + card grids after rendering. */
function fillFormValues() {
  var container = document.getElementById('char-fields');
  if (!container) return;
  container.querySelectorAll('select[data-options-from="sects"]').forEach(function (sel) {
    var wv = getCurrentWorldviewId();
    apiFetch('/sessions/models/sects?worldview=' + encodeURIComponent(wv)).then(function (r) { return r.json(); }).then(function (d) {
      var sects = d.sects || [];
      sel.innerHTML = '<option value="">无</option>' + sects.map(function (s) { return '<option value="' + _escH(s) + '">' + _escH(s) + '</option>'; }).join('');
    }).catch(function () {});
  });
  container.querySelectorAll('[data-options-from]').forEach(function (el) {
    var from = el.dataset.optionsFrom;
    if (from === 'sects') return;
    if (el.tagName === 'SELECT') return; // already filled at render time
  });
  // Card grids
  container.querySelectorAll('div[data-options-from]').forEach(function (grid) {
    if (!grid.id || grid.id.indexOf('grid-') !== 0) return;
    var from = grid.dataset.optionsFrom;
    var key = grid.dataset.key;
    var src = charTemplates && charTemplates[from];
    if (!Array.isArray(src)) return;
    grid.innerHTML = src.map(function (o) {
      var v = o.id != null ? o.id : o.value;
      return '<div class="gf-card" data-gf-id="' + _escH(v) + '" onclick="selectFormCard(\'' + _escH(key) + '\',\'' + _escH(v) + '\')" style="padding:8px;border:1px solid var(--border);border-radius:6px;cursor:pointer;text-align:center;transition:all .15s;background:var(--input-bg)">' +
        '<div style="font-size:20px;margin-bottom:4px">' + (o.icon || '') + '</div>' +
        '<div style="font-size:12px;color:var(--text)">' + _escH(o.name || o.label || v) + '</div></div>';
    }).join('') + (src.length ? '' : '');
  });
  // Clear card selection state on first render
  container.querySelectorAll('.gf-card').forEach(function (c) { c.style.borderColor = 'var(--border)'; });
}

function selectFormCard(key, id) {
  var container = document.getElementById('char-fields');
  container.querySelectorAll('#grid-' + key + ' .gf-card').forEach(function (c) {
    var sel = c.dataset.gfId === id;
    c.style.borderColor = sel ? 'var(--accent)' : 'var(--border)';
    c.style.background = sel ? 'rgba(201,169,110,0.15)' : 'var(--input-bg)';
  });
  var customWrap = document.getElementById('custom-' + key + '-wrap');
  if (customWrap) customWrap.style.display = id === '__custom__' ? 'block' : 'none';
}

/* Collect the form field values into the `fields` map sent to the backend. */
function collectFormValues() {
  var values = {};
  var container = document.getElementById('char-fields');
  if (!container) return values;
  var form = charTemplates && charTemplates.form;
  if (!form || !form.fields) return values; // legacy path sends old fields

  form.fields.forEach(function (f) {
    if (!_visibleIf(f)) return;
    var key = f.key || '';
    if (f.kind === 'card_grid') {
      var selected = container.querySelector('#grid-' + key + ' .gf-card[style*="var(--accent)"]');
      values[key] = selected ? selected.dataset.gfId : '';
      if (selected && selected.dataset.gfId === '__custom__') {
        values[key + '_custom'] = (document.getElementById('custom-' + key) || {}).value || '';
      }
    } else {
      var el = document.getElementById('field-' + key);
      if (el) {
        values[key] = el.value;
        if (el.value === '__custom__') {
          values[key + '_custom'] = (document.getElementById('custom-' + key) || {}).value || '';
        }
      }
    }
  });
  return values;
}

/* Legacy bindings + value filling (backward-compat with old applyCharacter). */
function bindLegacyFieldEvents() {
  var g = document.getElementById('char-gender');
  if (g && charTemplates && charTemplates.genders) g.innerHTML = charTemplates.genders.map(function(o){ return '<option value="' + _escH(o.value) + '">' + _escH(o.label) + '</option>'; }).join('');
  var bg = document.getElementById('char-background');
  if (bg && charTemplates && charTemplates.backgrounds) bg.innerHTML = charTemplates.backgrounds.map(function(o){ return '<option value="' + _escH(o.value) + '">' + _escH(o.label) + '</option>'; }).join('');
  var c = document.getElementById('char-cultivation');
  if (c && charTemplates && charTemplates.cultivations) c.innerHTML = charTemplates.cultivations.map(function(o){ return '<option value="' + _escH(o.value) + '">' + _escH(o.label) + ' — ' + _escH(o.desc) + '</option>'; }).join('');
  var p = document.getElementById('char-personality');
  if (p && charTemplates && charTemplates.personalities) p.innerHTML = charTemplates.personalities.map(function(o){ return '<option value="' + _escH(o.value) + '">' + _escH(o.label) + '</option>'; }).join('');
  var i = document.getElementById('char-identity');
  if (i && charTemplates && charTemplates.identities) {
    var ids = charTemplates.identities;
    i.innerHTML = Object.keys(ids).map(function(k){ return '<option value="' + _escH(k) + '">' + _escH(ids[k].label) + ' — ' + _escH(ids[k].desc) + '</option>'; }).join('');
  }
  // Golden finger grid
  var grid = document.getElementById('gf-grid');
  if (grid && charTemplates) {
    var gfs = charTemplates.golden_fingers || [];
    grid.innerHTML = gfs.map(function(gf){ return '<div class="gf-card" data-gf-id="' + _escH(gf.id) + '" onclick="selectGoldenFinger(\'' + _escH(gf.id) + '\')" style="padding:8px;border:1px solid var(--border);border-radius:6px;cursor:pointer;text-align:center;background:var(--input-bg)"><div style="font-size:20px;margin-bottom:4px">' + (gf.icon||'') + '</div><div style="font-size:12px;color:var(--text)">' + _escH(gf.name) + '</div></div>'; }).join('');
    document.querySelectorAll('#gf-grid .gf-card').forEach(function(x){ x.style.borderColor='var(--border)'; });
  }
  ['char-cultivation','char-personality','char-identity','char-background'].forEach(function(id){
    var el = document.getElementById(id);
    if (el) el.addEventListener('change', updateCharHints);
  });
}

function fillLegacyValues() {
  // fetch sects for legacy form
  var sectSel = document.getElementById('char-sect');
  if (sectSel) {
    apiFetch('/sessions/models/sects?worldview=' + encodeURIComponent(getCurrentWorldviewId())).then(function(r){ return r.json(); }).then(function(d){
      var sects = d.sects || [];
      sectSel.innerHTML = '<option value="">无宗门（散修）</option>' + sects.map(function(s){ return '<option value="' + _escH(s) + '">' + _escH(s) + '</option>'; }).join('');
      var sectRow = document.getElementById('sect-row');
      if (sectRow) sectRow.style.display = sects.length ? '' : 'none';
    }).catch(function(){});
  }
}
