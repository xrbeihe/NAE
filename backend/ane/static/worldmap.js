// Simplex noise — 2D implementation, public domain
// Based on Stefan Gustavson's implementation

class SimplexNoise {
  constructor(seed = (Math.random() * 65536) | 0) {
    this.grad3 = [
      [1,1,0],[-1,1,0],[1,-1,0],[-1,-1,0],
      [1,0,1],[-1,0,1],[1,0,-1],[-1,0,-1],
      [0,1,1],[0,-1,1],[0,1,-1],[0,-1,-1]
    ];
    this.p = [];
    for (let i = 0; i < 256; i++) this.p[i] = i;
    // Shuffle with seed (LCG, ensure positive)
    let s = Math.abs(seed) || 1;
    for (let i = 255; i > 0; i--) {
      s = (s * 16807) % 2147483647;
      if (s < 0) s = -s;
      if (s === 0) s = 1;
      const j = s % (i + 1);
      [this.p[i], this.p[j]] = [this.p[j], this.p[i]];
    }
    this.perm = [];
    for (let i = 0; i < 512; i++) this.perm[i] = this.p[i & 255];
  }

  dot(g, x, y) { return g[0] * x + g[1] * y; }

  noise2D(x, y) {
    const F2 = 0.5 * (Math.sqrt(3) - 1);
    const G2 = (3 - Math.sqrt(3)) / 6;
    const s = (x + y) * F2;
    const i = Math.floor(x + s);
    const j = Math.floor(y + s);
    const t = (i + j) * G2;
    const X0 = i - t, Y0 = j - t;
    const x0 = x - X0, y0 = y - Y0;
    let i1, j1;
    if (x0 > y0) { i1 = 1; j1 = 0; }
    else { i1 = 0; j1 = 1; }
    const x1 = x0 - i1 + G2;
    const y1 = y0 - j1 + G2;
    const x2 = x0 - 1 + 2 * G2;
    const y2 = y0 - 1 + 2 * G2;
    const ii = i & 255, jj = j & 255;
    const gi0 = this.perm[ii + this.perm[jj]] % 12;
    const gi1 = this.perm[ii + i1 + this.perm[jj + j1]] % 12;
    const gi2 = this.perm[ii + 1 + this.perm[jj + 1]] % 12;
    let n0 = 0, n1 = 0, n2 = 0;
    let t0 = 0.5 - x0 * x0 - y0 * y0;
    if (t0 >= 0) { t0 *= t0; n0 = t0 * t0 * this.dot(this.grad3[gi0], x0, y0); }
    let t1 = 0.5 - x1 * x1 - y1 * y1;
    if (t1 >= 0) { t1 *= t1; n1 = t1 * t1 * this.dot(this.grad3[gi1], x1, y1); }
    let t2 = 0.5 - x2 * x2 - y2 * y2;
    if (t2 >= 0) { t2 *= t2; n2 = t2 * t2 * this.dot(this.grad3[gi2], x2, y2); }
    return 70 * (n0 + n1 + n2);
  }
}

// Poisson disk sampling — generate evenly spaced points
function poissonDisk(radius, width, height, maxAttempts = 30) {
  const cellSize = radius / Math.SQRT2;
  const cols = Math.ceil(width / cellSize) || 1;
  const rows = Math.ceil(height / cellSize) || 1;
  const grid = new Array(cols * rows).fill(-1);
  const points = [];
  const active = [];
  const queue = [];

  const idx = (x, y) => Math.floor(x / cellSize) + Math.floor(y / cellSize) * cols;

  const distSq = (p1, p2) => (p1[0] - p2[0]) ** 2 + (p1[1] - p2[1]) ** 2;

  // Start with center
  const start = [width / 2 + (Math.random() - 0.5) * radius, height / 2 + (Math.random() - 0.5) * radius];
  points.push(start);
  active.push(points.length - 1);
  queue.push(start);

  while (queue.length > 0 && points.length < 1000) {
    const qi = Math.floor(Math.random() * queue.length);
    const center = queue[qi];
    let found = false;

    for (let attempt = 0; attempt < maxAttempts; attempt++) {
      const angle = Math.random() * 2 * Math.PI;
      const dist = radius * (1 + Math.random());
      const x = center[0] + Math.cos(angle) * dist;
      const y = center[1] + Math.sin(angle) * dist;
      if (x < 10 || x >= width - 10 || y < 10 || y >= height - 10) continue;
      const gi = idx(x, y);
      const cx = Math.floor(x / cellSize), cy = Math.floor(y / cellSize);
      let ok = true;
      for (let dx = -2; dx <= 2 && ok; dx++) {
        for (let dy = -2; dy <= 2 && ok; dy++) {
          const ni = (cx + dx) + (cy + dy) * cols;
          if (ni >= 0 && ni < grid.length && grid[ni] >= 0) {
            const pi = grid[ni];
            if (distSq([x, y], points[pi]) < radius * radius) ok = false;
          }
        }
      }
      if (ok) {
        points.push([x, y]);
        grid[gi] = points.length - 1;
        queue.push([x, y]);
        found = true;
        break;
      }
    }
    if (!found) queue.splice(qi, 1);
  }
  return points;
}

// Color interpolation helpers
function lerp(a, b, t) { return a + (b - a) * t; }

function lerpColor(c1, c2, t) {
  return [
    Math.round(lerp(c1[0], c2[0], t)),
    Math.round(lerp(c1[1], c2[1], t)),
    Math.round(lerp(c1[2], c2[2], t))
  ];
}

function rgb(r, g, b) { return `rgb(${r},${g},${b})`; }

// Main world map renderer
class WorldMapRenderer {
  constructor(canvas, options = {}) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d', { willReadFrequently: true });

    // HiDPI support: scale canvas buffer by devicePixelRatio
    const dpr = window.devicePixelRatio || 1;
    this.dpr = dpr;
    const logicalW = options.width || 800;
    const logicalH = options.height || 600;
    this.logicalWidth = logicalW;
    this.logicalHeight = logicalH;
    this.width = Math.floor(logicalW * dpr);
    this.height = Math.floor(logicalH * dpr);
    this.canvas.width = this.width;
    this.canvas.height = this.height;
    this.canvas.style.width = logicalW + 'px';
    this.canvas.style.height = logicalH + 'px';
    this.ctx.scale(dpr, dpr);

    this.seed = options.seed || (Math.random() * 65536) | 0;
    this.noise = new SimplexNoise(this.seed);
    this.locations = options.locations || []; // [{name, desc}]
    this.cityLocations = options.cityLocations || []; // [{name, x, y}] — one per sect
    this.playerLocation = options.playerLocation || '';  // name of current city/sect
    this.playerGender = options.playerGender || '男';      // player gender for marker emoji
    this.onMoveTo = options.onMoveTo || null;             // callback(destName, destX, destY)
    this.sectDragOffsetX = 0;   // user-draggable offset for sect labels
    this.sectDragOffsetY = 0;
    this.markerDragging = false;    // player marker drag active
    this.markerDragOfsX = 0;        // temp visual offset during marker drag
    this.markerDragOfsY = 0;
    this._moveLock = false;         // prevent concurrent move calls
    this.terrainImage = null;

    // Bind events
    this._bindEvents();
  }

  // ── Terrain palette ──
  _getTerrainColor(h) {
    const palette = [
      { limit: -0.35, color: [15, 28, 60] },      // deep water
      { limit: -0.15, color: [25, 60, 95] },      // shallow water
      { limit: 0.10, color: [60, 100, 60] },      // coast / sand
      { limit: 0.20, color: [80, 125, 65] },      // plains
      { limit: 0.30, color: [60, 100, 50] },      // forest
      { limit: 0.40, color: [100, 85, 55] },      // hills
      { limit: 0.55, color: [120, 95, 70] },      // mountains
      { limit: 0.70, color: [160, 140, 120] },    // high mountain
      { limit: 1.0, color: [220, 210, 200] },     // snow
    ];
    if (h <= palette[0].limit) return palette[0].color;
    for (let i = 0; i < palette.length - 1; i++) {
      if (h > palette[i].limit && h <= palette[i + 1].limit) {
        const t = (h - palette[i].limit) / (palette[i + 1].limit - palette[i].limit);
        return lerpColor(palette[i].color, palette[i + 1].color, t);
      }
    }
    return palette[palette.length - 1].color;
  }

  // ── Generate terrain image (called once) ──
  generateTerrain() {
    const scale = 0.002;
    const octaves = 6;
    const persistence = 0.55;
    const imageData = this.ctx.createImageData(this.width, this.height);
    const data = imageData.data;

    for (let y = 0; y < this.height; y++) {
      for (let x = 0; x < this.width; x++) {
        let h = 0, amp = 1, freq = 1, maxAmp = 0;
        for (let o = 0; o < octaves; o++) {
          h += this.noise.noise2D((x / this.dpr) * scale * freq + 1000, (y / this.dpr) * scale * freq + 1000) * amp;
          maxAmp += amp;
          amp *= persistence;
          freq *= 2;
        }
        h /= maxAmp;
        h += 0.15; // sea level offset: lift distribution, less blue water
        const c = this._getTerrainColor(h);
        const idx = (y * this.width + x) * 4;
        data[idx] = c[0];
        data[idx + 1] = c[1];
        data[idx + 2] = c[2];
        data[idx + 3] = 255;
      }
    }
    // Draw water outline
    this.ctx.putImageData(imageData, 0, 0);
    this._renderCoastGlow();
    this.terrainImage = this.ctx.getImageData(0, 0, this.width, this.height);
  }

  _renderCoastGlow() {
    const ctx = this.ctx;
    const w = this.width, h = this.height;
    const img = ctx.getImageData(0, 0, w, h);
    const d = img.data;

    // Simple coast edge detection: if a water pixel touches a land pixel, brighten it
    const isWater = (x, y) => {
      if (x < 0 || x >= w || y < 0 || y >= h) return false;
      const idx = (y * w + x) * 4;
      const b = d[idx + 2];
      const r = d[idx];
      // Deep water: low red, high blue ratio
      return b > r + 20 && r < 80;
    };

    for (let y = 0; y < h; y++) {
      for (let x = 0; x < w; x++) {
        const idx = (y * w + x) * 4;
        if (!isWater(x, y)) continue;
        // Check if any neighbor is land
        let nearLand = false;
        for (let dx = -2; dx <= 2 && !nearLand; dx++) {
          for (let dy = -2; dy <= 2 && !nearLand; dy++) {
            if (dx === 0 && dy === 0) continue;
            if (!isWater(x + dx, y + dy)) nearLand = true;
          }
        }
        if (nearLand) {
          d[idx] = Math.min(d[idx] + 25, 255);
          d[idx + 1] = Math.min(d[idx + 1] + 35, 255);
          d[idx + 2] = Math.min(d[idx + 2] + 20, 255);
        }
      }
    }
    ctx.putImageData(img, 0, 0);
  }

  // ── Render city labels (bottom layer, fixed position) ──
  _renderCityLabels(ctx) {
    const cities = this.cityLocations;
    if (!cities.length) return;

    ctx.save();
    ctx.globalAlpha = 0.85;
    for (const city of cities) {
      const px = city.x;
      const py = city.y;
      const name = city.name || "?";

      ctx.font = 'bold 12px "Microsoft YaHei", sans-serif';
      const tw = ctx.measureText(name).width;
      const pad = 8;
      const bw = tw + pad * 2 + 4;
      const bh = 18 + pad * 2;

      // Urban-style label: teal background
      const cx = px - bw / 2;
      const cy = py - bh / 2;
      ctx.shadowColor = 'rgba(0,0,0,0.5)';
      ctx.shadowBlur = 6;
      ctx.shadowOffsetX = 1;
      ctx.shadowOffsetY = 1;
      ctx.fillStyle = 'rgba(30, 80, 95, 0.88)';
      ctx.beginPath();
      const r = 4;
      ctx.moveTo(cx + r, cy);
      ctx.lineTo(cx + bw - r, cy);
      ctx.quadraticCurveTo(cx + bw, cy, cx + bw, cy + r);
      ctx.lineTo(cx + bw, cy + bh - r);
      ctx.quadraticCurveTo(cx + bw, cy + bh, cx + bw - r, cy + bh);
      ctx.lineTo(cx + r, cy + bh);
      ctx.quadraticCurveTo(cx, cy + bh, cx, cy + bh - r);
      ctx.lineTo(cx, cy + r);
      ctx.quadraticCurveTo(cx, cy, cx + r, cy);
      ctx.closePath();
      ctx.fill();

      ctx.shadowColor = 'transparent';
      ctx.strokeStyle = '#5a9aaa';
      ctx.lineWidth = 1;
      ctx.stroke();

      ctx.fillStyle = '#d4eef5';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(name, px, py);
    }
    ctx.restore();
  }

  // ── Render sect labels (top layer, draggable) ──
  _renderSectLabels(ctx) {
    const locations = this.locations;
    if (!locations.length) return;

    const sx = this.scale || 1;
    const sy = this.scale || 1;
    const ox = -(this.offsetX || 0) / (this.scale || 1);
    const oy = -(this.offsetY || 0) / (this.scale || 1);

    ctx.save();
    ctx.scale(sx, sy);
    ctx.translate(ox, oy);

    // Compute + draw same boxes as _renderLabels
    const boxes = [];
    for (const loc of locations) {
      ctx.font = 'bold 12px "Microsoft YaHei", sans-serif';
      const tw = ctx.measureText(loc.name).width;
      const pad = 6;
      const bw = tw + pad * 2 + 6;
      const bh = 18 + pad * 2;
      boxes.push({ loc, bw, bh, baseX: loc.x, baseY: loc.y, ox: 0, oy: 0 });
    }

    for (let pass = 0; pass < 5; pass++) {
      let moved = false;
      for (let i = 0; i < boxes.length; i++) {
        for (let j = i + 1; j < boxes.length; j++) {
          const a = boxes[i], b = boxes[j];
          const ax1 = a.baseX + a.ox - a.bw / 2, ax2 = a.baseX + a.ox + a.bw / 2;
          const ay1 = a.baseY + a.oy - a.bh / 2, ay2 = a.baseY + a.oy + a.bh / 2;
          const bx1 = b.baseX + b.ox - b.bw / 2, bx2 = b.baseX + b.ox + b.bw / 2;
          const by1 = b.baseY + b.oy - b.bh / 2, by2 = b.baseY + b.oy + b.bh / 2;
          const overlapX = Math.min(ax2, bx2) - Math.max(ax1, bx1);
          const overlapY = Math.min(ay2, by2) - Math.max(ay1, by1);
          if (overlapX > 0 && overlapY > 0) {
            if (overlapX < overlapY) {
              const sign = (a.baseX + a.ox) < (b.baseX + b.ox) ? -1 : 1;
              a.ox -= sign * (overlapX / 2 + 2);
              b.ox += sign * (overlapX / 2 + 2);
            } else {
              const sign = (a.baseY + a.oy) < (b.baseY + b.oy) ? -1 : 1;
              a.oy -= sign * (overlapY / 2 + 2);
              b.oy += sign * (overlapY / 2 + 2);
            }
            moved = true;
          }
        }
      }
      if (!moved) break;
    }

    for (const box of boxes) {
      const px = box.baseX + box.ox;
      const py = box.baseY + box.oy;
      const name = box.loc.name;
      const bw = box.bw, bh = box.bh;

      ctx.shadowColor = 'rgba(0,0,0,0.5)';
      ctx.shadowBlur = 8;
      ctx.shadowOffsetX = 2;
      ctx.shadowOffsetY = 2;

      const cx = px - bw / 2;
      const cy = py - bh / 2;
      ctx.fillStyle = 'rgba(245, 235, 210, 0.92)';
      ctx.beginPath();
      const r = 6;
      ctx.moveTo(cx + r, cy);
      ctx.lineTo(cx + bw - r, cy);
      ctx.quadraticCurveTo(cx + bw, cy, cx + bw, cy + r);
      ctx.lineTo(cx + bw, cy + bh - r);
      ctx.quadraticCurveTo(cx + bw, cy + bh, cx + bw - r, cy + bh);
      ctx.lineTo(cx + r, cy + bh);
      ctx.quadraticCurveTo(cx, cy + bh, cx, cy + bh - r);
      ctx.lineTo(cx, cy + r);
      ctx.quadraticCurveTo(cx, cy, cx + r, cy);
      ctx.closePath();
      ctx.fill();

      ctx.shadowColor = 'transparent';
      ctx.strokeStyle = '#8b7355';
      ctx.lineWidth = 1.5;
      ctx.stroke();

      ctx.fillStyle = '#3a2a1a';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(name, px, py);
    }

    ctx.restore();
  }

  // ── Main render ──
  render() {
    const ctx = this.ctx;
    ctx.clearRect(0, 0, this.logicalWidth, this.logicalHeight);

    // Draw terrain (always full size)
    ctx.save();
    if (this.terrainImage) {
      ctx.putImageData(this.terrainImage, 0, 0);
    } else {
      this.generateTerrain();
    }
    ctx.restore();

    // Layer 1: city labels — fixed, no scale/offset
    this._renderCityLabels(ctx);

    // Layer 2: sect labels with drag offset
    ctx.save();
    ctx.translate(this.sectDragOffsetX, this.sectDragOffsetY);
    this._renderSectLabels(ctx);
    ctx.restore();

    // Layer 3: player marker (topmost, above sect layer — always clickable)
    this._renderPlayerMarker(ctx);

    // Hint text (top, center-aligned)
    const hint = this.playerLocation
      ? '拖拽🧍小人可移动位置 | 点击宗门/城市可前往'
      : '拖拽宗门标签可查看下方城市';
    ctx.shadowColor = 'rgba(0,0,0,0.7)';
    ctx.shadowBlur = 5;
    ctx.shadowOffsetX = 1;
    ctx.shadowOffsetY = 1;
    ctx.fillStyle = 'rgba(220, 210, 180, 0.85)';
    ctx.font = '12px "Microsoft YaHei", sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'top';
    ctx.fillText(hint, this.logicalWidth / 2, 4);
    ctx.shadowColor = 'transparent';
    ctx.shadowBlur = 0;

    // Watermark
    ctx.fillStyle = 'rgba(255,255,255,0.05)';
    ctx.font = '12px sans-serif';
    ctx.textAlign = 'right';
    ctx.fillText(`seed: ${Math.round(this.seed)}`, this.logicalWidth - 10, this.logicalHeight - 10);
  }

  // ── Player marker ──
  _renderPlayerMarker(ctx) {
    const loc = this.playerLocation;
    if (!loc) return;

    // Find the city/sect that matches playerLocation
    let px = null, py = null;
    for (const city of this.cityLocations) {
      if (city.name === loc) { px = city.x; py = city.y; break; }
    }
    if (px === null) {
      for (const s of this.locations) {
        if (s.name === loc) { px = s.x; py = s.y; break; }
      }
    }
    if (px === null) return;

    // Apply drag offset
    const drawX = px + this.markerDragOfsX;
    const drawY = py + this.markerDragOfsY;

    const emoji = this.playerGender === '女' ? '🧍‍♀️' : '🧍‍♂️';

    ctx.save();
    // White backing circle to make emoji pop against terrain
    ctx.shadowColor = 'rgba(0, 0, 0, 0.6)';
    ctx.shadowBlur = 12;
    ctx.beginPath();
    ctx.arc(drawX, drawY - 22, 13, 0, Math.PI * 2);
    ctx.fillStyle = 'rgba(255, 255, 255, 0.85)';
    ctx.fill();
    // Golden glow ring
    ctx.shadowColor = 'rgba(255, 200, 80, 0.5)';
    ctx.shadowBlur = 10;
    ctx.beginPath();
    ctx.arc(drawX, drawY - 22, 14, 0, Math.PI * 2);
    ctx.strokeStyle = 'rgba(255, 200, 80, 0.5)';
    ctx.lineWidth = 2;
    ctx.stroke();
    ctx.shadowColor = 'transparent';
    ctx.shadowBlur = 0;
    // Draw emoji
    ctx.font = '22px "Segoe UI Emoji", "Apple Color Emoji", "Noto Color Emoji", sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'bottom';
    ctx.fillText(emoji, drawX, drawY - 16);
    ctx.restore();
  }

  // ── Click detection: find what was clicked and trigger move ──
  _handleClick(e) {
    if (!this.onMoveTo) return;

    const rect = this.canvas.getBoundingClientRect();
    // Get logical coordinates (account for canvas DPR scaling)
    const logicalX = (e.clientX - rect.left) * (this.logicalWidth / rect.width);
    const logicalY = (e.clientY - rect.top) * (this.logicalHeight / rect.height);

    // Debounce: same position within 800ms → ignore
    const now = Date.now();
    if (this._lastClick && (now - this._lastClick.time < 800)) {
      const dx = logicalX - this._lastClick.x;
      const dy = logicalY - this._lastClick.y;
      if (Math.hypot(dx, dy) < 10) return;
    }
    this._lastClick = { x: logicalX, y: logicalY, time: now };

    // Check city labels first (fixed positions)
    const hitRadius = 20;
    for (const city of this.cityLocations) {
      if (Math.abs(logicalX - city.x) < hitRadius && Math.abs(logicalY - city.y) < hitRadius) {
        if (city.name !== this.playerLocation) {
          this.onMoveTo(city.name, city.x, city.y);
          return;
        }
      }
    }
    // Then sect labels (with drag offset applied)
    const adjX = logicalX - this.sectDragOffsetX;
    const adjY = logicalY - this.sectDragOffsetY;
    for (const loc of this.locations) {
      if (Math.abs(adjX - loc.x) < hitRadius && Math.abs(adjY - loc.y) < hitRadius) {
        if (loc.name !== this.playerLocation) {
          this.onMoveTo(loc.name, loc.x, loc.y);
          return;
        }
      }
    }
  }

  _getPlayerMarkerCoords() {
    const loc = this.playerLocation;
    if (!loc) return null;
    for (const city of this.cityLocations) {
      if (city.name === loc) return { x: city.x, y: city.y };
    }
    for (const s of this.locations) {
      if (s.name === loc) return { x: s.x, y: s.y };
    }
    return null;
  }

  // ── Interaction ──
  _bindEvents() {
    // Wheel zoom
    this.canvas.addEventListener('wheel', (e) => {
      e.preventDefault();
      const rect = this.canvas.getBoundingClientRect();
      const mx = e.clientX - rect.left;
      const my = e.clientY - rect.top;
      const oldScale = this.scale || 1;
      const delta = e.deltaY > 0 ? 0.9 : 1.1;
      this.scale = Math.max(0.5, Math.min(5, (this.scale || 1) * delta));
      this.offsetX = mx - (mx - (this.offsetX || 0)) * (this.scale / oldScale);
      this.offsetY = my - (my - (this.offsetY || 0)) * (this.scale / oldScale);
      this.render();
    });

    // ── Shared coordinate helper ──
    const _logicalCoords = (e) => {
      const rect = this.canvas.getBoundingClientRect();
      return {
        x: (e.clientX - rect.left) * (this.logicalWidth / rect.width),
        y: (e.clientY - rect.top) * (this.logicalHeight / rect.height),
      };
    };

    // ── Mousedown: detect hit target ──
    let _activeMode = null;       // 'sect' | 'marker' | null
    let _dragStartX = 0, _dragStartY = 0;
    let _clickX = 0, _clickY = 0;
    let _origSectOfsX = 0, _origSectOfsY = 0;
    let _origMarkerX = 0, _origMarkerY = 0;

    this.canvas.addEventListener('mousedown', (e) => {
      const lc = _logicalCoords(e);
      _clickX = e.clientX; _clickY = e.clientY;

      // Check marker hit first (higher z-order)
      const mpos = this._getPlayerMarkerCoords();
      if (mpos) {
        const dist = Math.hypot(lc.x - mpos.x, lc.y - (mpos.y - 12));
        if (dist < 22 && this.playerLocation) {
          // Start marker drag
          _activeMode = 'marker';
          _origMarkerX = mpos.x;
          _origMarkerY = mpos.y;
          this.markerDragging = true;
          this.markerDragOfsX = 0;
          this.markerDragOfsY = 0;
          _dragStartX = e.clientX;
          _dragStartY = e.clientY;
          this.canvas.style.cursor = 'grabbing';
          return;
        }
      }

      // Otherwise: sect drag
      _activeMode = 'sect';
      _origSectOfsX = this.sectDragOffsetX;
      _origSectOfsY = this.sectDragOffsetY;
      _dragStartX = e.clientX;
      _dragStartY = e.clientY;
      this.canvas.style.cursor = 'grabbing';
    });

    window.addEventListener('mousemove', (e) => {
      if (!_activeMode) return;
      const dx = e.clientX - _dragStartX;
      const dy = e.clientY - _dragStartY;

      if (_activeMode === 'marker') {
        this.markerDragOfsX = dx;
        this.markerDragOfsY = dy;
        this.canvas.style.cursor = 'grabbing';
        this.render();
      } else if (_activeMode === 'sect') {
        this.sectDragOffsetX = _origSectOfsX + dx;
        this.sectDragOffsetY = _origSectOfsY + dy;
        this.canvas.style.cursor = 'grabbing';
        this.render();
      }
    });

    window.addEventListener('mouseup', (e) => {
      if (!_activeMode) return;
      const dx = e.clientX - _clickX;
      const dy = e.clientY - _clickY;
      const isClick = Math.abs(dx) < 5 && Math.abs(dy) < 5;

      if (_activeMode === 'marker') {
        if (isClick) {
          // Click on marker: show current location
          this._handleMarkerClick();
        } else {
          // Drag ended: find nearest city/sect
          this._handleMarkerDrop(e);
        }
        this.markerDragging = false;
        this.markerDragOfsX = 0;
        this.markerDragOfsY = 0;
      } else if (_activeMode === 'sect') {
        if (isClick) {
          // Cancel drag offset so _handleClick sees correct coords
          this.sectDragOffsetX = _origSectOfsX;
          this.sectDragOffsetY = _origSectOfsY;
          this._handleClick(e);
        }
        // If dragged, keep the offset as-is (user positioned labels)
      }

      _activeMode = null;
      this.canvas.style.cursor = '';
      this.render();
    });

    // ── Touch ──
    let _touchStartX = 0, _touchStartY = 0;
    let _touchOrigSectX = 0, _touchOrigSectY = 0;

    this.canvas.addEventListener('touchstart', (e) => {
      if (e.touches.length !== 1) return;
      const touch = e.touches[0];
      _touchStartX = touch.clientX; _touchStartY = touch.clientY;
      const rect = this.canvas.getBoundingClientRect();
      const lx = (touch.clientX - rect.left) * (this.logicalWidth / rect.width);
      const ly = (touch.clientY - rect.top) * (this.logicalHeight / rect.height);

      const mpos = this._getPlayerMarkerCoords();
      if (mpos && Math.hypot(lx - mpos.x, ly - mpos.y) < 22 && this.playerLocation) {
        // Marker drag
        _activeMode = 'marker';
        _origMarkerX = mpos.x;
        _origMarkerY = mpos.y;
        this.markerDragging = true;
        this.markerDragOfsX = 0;
        this.markerDragOfsY = 0;
        _dragStartX = touch.clientX;
        _dragStartY = touch.clientY;
      } else {
        // Sect drag
        _activeMode = 'sect';
        _origSectOfsX = this.sectDragOffsetX;
        _origSectOfsY = this.sectDragOffsetY;
        _dragStartX = touch.clientX;
        _dragStartY = touch.clientY;
      }
    });

    this.canvas.addEventListener('touchmove', (e) => {
      if (!_activeMode || e.touches.length !== 1) return;
      const touch = e.touches[0];
      const dx = touch.clientX - _dragStartX;
      const dy = touch.clientY - _dragStartY;
      const rect = this.canvas.getBoundingClientRect();
      const scaleX = this.logicalWidth / rect.width;
      const scaleY = this.logicalHeight / rect.height;

      if (_activeMode === 'marker') {
        this.markerDragOfsX = dx * scaleX;
        this.markerDragOfsY = dy * scaleY;
        this.render();
      } else if (_activeMode === 'sect') {
        this.sectDragOffsetX = _origSectOfsX + dx * scaleX;
        this.sectDragOffsetY = _origSectOfsY + dy * scaleY;
        this.render();
      }
      e.preventDefault();
    });

    this.canvas.addEventListener('touchend', (e) => {
      if (!_activeMode) return;
      const touch = e.changedTouches[0];
      const dx = touch.clientX - _touchStartX;
      const dy = touch.clientY - _touchStartY;
      const isTap = Math.abs(dx) < 5 && Math.abs(dy) < 5;

      if (_activeMode === 'marker') {
        if (isTap) {
          this._handleMarkerClick();
        } else {
          this._handleMarkerDrop(touch);
        }
        this.markerDragging = false;
        this.markerDragOfsX = 0;
        this.markerDragOfsY = 0;
      } else if (_activeMode === 'sect') {
        if (isTap) {
          this.sectDragOffsetX = _origSectOfsX;
          this.sectDragOffsetY = _origSectOfsY;
          this._handleClick({ clientX: _touchStartX, clientY: _touchStartY });
        }
      }

      _activeMode = null;
      this.render();
    });

    // ── Click detection: find what was clicked and trigger move ──
    // (renamed helper, called from mouseup/touchend)
  } // end _bindEvents

  // ── Handle click on player marker ──
  _handleMarkerClick() {
    if (!this.playerLocation || this._moveLock) return;
    this._moveLock = true;
    setTimeout(() => { this._moveLock = false; }, 500);
    // Show a sticky card in the chat — dispatched via callback
    if (this.onMoveTo) {
      // onMoveTo with only one param means "marker clicked" — show location
      this.onMoveTo(this.playerLocation, -1, -1);
    }
  }

  // ── Handle marker drop: find nearest city/sect ──
  _handleMarkerDrop(e) {
    if (!this.playerLocation || !this.onMoveTo || this._moveLock) return;

    const rect = this.canvas.getBoundingClientRect();
    const logicalX = (e.clientX - rect.left) * (this.logicalWidth / rect.width);
    const logicalY = (e.clientY - rect.top) * (this.logicalHeight / rect.height);

    // Find nearest city/sect within threshold
    const threshold = 40;
    let nearest = null;
    let nearestDist = Infinity;

    for (const city of this.cityLocations) {
      if (city.name === this.playerLocation) continue;
      const d = Math.hypot(logicalX - city.x, logicalY - city.y);
      if (d < threshold && d < nearestDist) {
        nearestDist = d;
        nearest = city;
      }
    }
    for (const loc of this.locations) {
      if (loc.name === this.playerLocation) continue;
      const d = Math.hypot(logicalX - loc.x, logicalY - loc.y);
      if (d < threshold && d < nearestDist) {
        nearestDist = d;
        nearest = loc;
      }
    }

    if (nearest) {
      this.onMoveTo(nearest.name, nearest.x, nearest.y, true);
    }
    // If no valid drop target, marker snaps back (render resets markerDragOfs)
  }
}
