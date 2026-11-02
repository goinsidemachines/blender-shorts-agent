/* A small QR code encoder for the phone link (ISO/IEC 18004): byte mode, error correction level M, versions 1 to 10.
   BSA.qr(text, {mask}) -> {size, modules}: modules[row][col] is true for a dark module. The text is encoded as
   ISO-8859-1 when every character fits, else as UTF-8 (as most encoders do). The mask is the one with the lowest
   standard penalty unless opts.mask (0..7) is given. Nothing here touches the page. */
(function () {
  "use strict";
  const BSA = (window.BSA = window.BSA || {});

  // Level M, versions 1..10: [error correction codewords per block, [blocks, data codewords per block]...]
  const BLOCKS = [
    null,
    [10, [1, 16]], [16, [1, 28]], [26, [1, 44]], [18, [2, 32]], [24, [2, 43]],
    [16, [4, 27]], [18, [4, 31]], [22, [2, 38], [2, 39]], [22, [3, 36], [2, 37]], [26, [4, 43], [1, 44]],
  ];
  const ALIGN = [null, [], [6, 18], [6, 22], [6, 26], [6, 30], [6, 34], [6, 22, 38], [6, 24, 42], [6, 26, 46],
    [6, 28, 50]];
  const MAX_VERSION = 10;
  const ECL_M = 0; // the format bits of level M

  // ------------------------------------------------------------------ GF(256), polynomial 0x11d

  const EXP = new Array(512);
  const LOG = new Array(256);
  (function () {
    let x = 1;
    for (let i = 0; i < 255; i++) {
      EXP[i] = x;
      LOG[x] = i;
      x <<= 1;
      if (x & 0x100) x ^= 0x11d;
    }
    for (let i = 255; i < 512; i++) EXP[i] = EXP[i - 255];
  })();

  function mul(a, b) {
    return a === 0 || b === 0 ? 0 : EXP[LOG[a] + LOG[b]];
  }

  // The generator polynomial of the given degree, highest power first (its leading 1 left out).
  function generator(degree) {
    let poly = [1];
    for (let i = 0; i < degree; i++) {
      const next = new Array(poly.length + 1).fill(0);
      for (let j = 0; j < poly.length; j++) {
        next[j] ^= poly[j];
        next[j + 1] ^= mul(poly[j], EXP[i]);
      }
      poly = next;
    }
    return poly.slice(1);
  }

  function remainder(data, gen) {
    const rem = new Array(gen.length).fill(0);
    for (const b of data) {
      const factor = b ^ rem.shift();
      rem.push(0);
      for (let i = 0; i < gen.length; i++) rem[i] ^= mul(gen[i], factor);
    }
    return rem;
  }

  // ------------------------------------------------------------------ data

  function toBytes(text) {
    const chars = Array.from(String(text));
    if (chars.every((c) => c.codePointAt(0) <= 0xff)) return chars.map((c) => c.codePointAt(0));
    return Array.from(new TextEncoder().encode(String(text)));
  }

  function dataCodewords(version) {
    const spec = BLOCKS[version];
    let n = 0;
    for (let i = 1; i < spec.length; i++) n += spec[i][0] * spec[i][1];
    return n;
  }

  function countBits(version) {
    return version < 10 ? 8 : 16;
  }

  function pickVersion(n) {
    for (let v = 1; v <= MAX_VERSION; v++) {
      if (4 + countBits(v) + 8 * n <= dataCodewords(v) * 8) return v;
    }
    throw new Error(`The text is too long for a QR code here (${n} bytes).`);
  }

  function codewords(bytes, version) {
    const bits = [];
    const put = (value, len) => { for (let i = len - 1; i >= 0; i--) bits.push((value >>> i) & 1); };
    const capacity = dataCodewords(version) * 8;
    put(0b0100, 4);
    put(bytes.length, countBits(version));
    for (const b of bytes) put(b, 8);
    put(0, Math.min(4, capacity - bits.length));
    // Pad to a whole codeword. Like segno (the reference the tests compare with), a stream that already ends on a
    // codeword boundary gets a whole zero codeword before the pad codewords; readers stop at the terminator, so it
    // reads the same either way.
    put(0, 8 - (bits.length % 8));
    bits.length = Math.min(bits.length, capacity);
    const data = [];
    for (let i = 0; i < bits.length; i += 8) {
      let b = 0;
      for (let j = 0; j < 8; j++) b = (b << 1) | bits[i + j];
      data.push(b);
    }
    for (let pad = 0xec; data.length < capacity / 8; pad ^= 0xec ^ 0x11) data.push(pad);

    // split into blocks, add each block's error correction, then interleave
    const spec = BLOCKS[version];
    const gen = generator(spec[0]);
    const blocks = [];
    let at = 0;
    for (let g = 1; g < spec.length; g++) {
      for (let k = 0; k < spec[g][0]; k++) {
        const d = data.slice(at, at + spec[g][1]);
        at += spec[g][1];
        blocks.push({ d, ec: remainder(d, gen) });
      }
    }
    const out = [];
    const longest = Math.max(...blocks.map((b) => b.d.length));
    for (let i = 0; i < longest; i++) for (const b of blocks) if (i < b.d.length) out.push(b.d[i]);
    for (let i = 0; i < spec[0]; i++) for (const b of blocks) out.push(b.ec[i]);
    return out;
  }

  // ------------------------------------------------------------------ the matrix

  function bch(value, poly, polyBits) {
    let v = value << (polyBits - 1);
    for (let i = 31 - Math.clz32(v); i >= polyBits - 1; i--) if ((v >>> i) & 1) v ^= poly << (i - (polyBits - 1));
    return (value << (polyBits - 1)) | v;
  }

  function makeGrid(version) {
    const size = version * 4 + 17;
    const grid = { size, dark: [], fixed: [] };
    for (let y = 0; y < size; y++) {
      grid.dark.push(new Array(size).fill(false));
      grid.fixed.push(new Array(size).fill(false));
    }
    return grid;
  }

  function setFixed(grid, x, y, dark) {
    grid.dark[y][x] = dark;
    grid.fixed[y][x] = true;
  }

  function drawFunctionPatterns(grid, version) {
    const size = grid.size;
    for (let i = 0; i < size; i++) { // timing patterns
      setFixed(grid, 6, i, i % 2 === 0);
      setFixed(grid, i, 6, i % 2 === 0);
    }
    for (const [cx, cy] of [[3, 3], [size - 4, 3], [3, size - 4]]) { // finders with their separators
      for (let dy = -4; dy <= 4; dy++) {
        for (let dx = -4; dx <= 4; dx++) {
          const x = cx + dx;
          const y = cy + dy;
          if (x < 0 || y < 0 || x >= size || y >= size) continue;
          const d = Math.max(Math.abs(dx), Math.abs(dy));
          setFixed(grid, x, y, d !== 2 && d !== 4);
        }
      }
    }
    const pos = ALIGN[version];
    const last = pos.length - 1;
    for (let i = 0; i < pos.length; i++) {
      for (let j = 0; j < pos.length; j++) {
        if ((i === 0 && j === 0) || (i === 0 && j === last) || (i === last && j === 0)) continue;
        for (let dy = -2; dy <= 2; dy++) {
          for (let dx = -2; dx <= 2; dx++) setFixed(grid, pos[i] + dx, pos[j] + dy, Math.max(Math.abs(dx), Math.abs(dy)) !== 1);
        }
      }
    }
    drawFormat(grid, 0); // reserves the format areas; drawn again once the mask is known
    if (version >= 7) {
      const bits = bch(version, 0x1f25, 13);
      for (let i = 0; i < 18; i++) {
        const dark = ((bits >>> i) & 1) === 1;
        const a = size - 11 + (i % 3);
        const b = Math.floor(i / 3);
        setFixed(grid, a, b, dark);
        setFixed(grid, b, a, dark);
      }
    }
  }

  function drawFormat(grid, mask) {
    const size = grid.size;
    const bits = bch((ECL_M << 3) | mask, 0x537, 11) ^ 0x5412;
    const bit = (i) => ((bits >>> i) & 1) === 1;
    for (let i = 0; i <= 5; i++) setFixed(grid, 8, i, bit(i));
    setFixed(grid, 8, 7, bit(6));
    setFixed(grid, 8, 8, bit(7));
    setFixed(grid, 7, 8, bit(8));
    for (let i = 9; i < 15; i++) setFixed(grid, 14 - i, 8, bit(i));
    for (let i = 0; i < 8; i++) setFixed(grid, size - 1 - i, 8, bit(i));
    for (let i = 8; i < 15; i++) setFixed(grid, 8, size - 15 + i, bit(i));
    setFixed(grid, 8, size - 8, true); // the dark module
  }

  function drawCodewords(grid, data) {
    const size = grid.size;
    let i = 0;
    for (let right = size - 1; right >= 1; right -= 2) {
      if (right === 6) right = 5;
      const upward = ((right + 1) & 2) === 0;
      for (let vert = 0; vert < size; vert++) {
        const y = upward ? size - 1 - vert : vert;
        for (let j = 0; j < 2; j++) {
          const x = right - j;
          if (grid.fixed[y][x]) continue;
          grid.dark[y][x] = i < data.length * 8 && ((data[i >>> 3] >>> (7 - (i & 7))) & 1) === 1;
          i++;
        }
      }
    }
  }

  const MASKS = [
    (x, y) => (x + y) % 2 === 0,
    (x, y) => y % 2 === 0,
    (x) => x % 3 === 0,
    (x, y) => (x + y) % 3 === 0,
    (x, y) => (Math.floor(x / 3) + Math.floor(y / 2)) % 2 === 0,
    (x, y) => ((x * y) % 2) + ((x * y) % 3) === 0,
    (x, y) => (((x * y) % 2) + ((x * y) % 3)) % 2 === 0,
    (x, y) => (((x + y) % 2) + ((x * y) % 3)) % 2 === 0,
  ];

  function applyMask(grid, mask) {
    const f = MASKS[mask];
    for (let y = 0; y < grid.size; y++) {
      for (let x = 0; x < grid.size; x++) if (!grid.fixed[y][x] && f(x, y)) grid.dark[y][x] = !grid.dark[y][x];
    }
  }

  // The standard penalty (rules N1 to N4) of a finished matrix: lower is easier to read.
  function penalty(m) {
    const size = m.length;
    let score = 0;
    const lines = [];
    for (let i = 0; i < size; i++) {
      lines.push(m[i]);
      lines.push(m.map((row) => row[i]));
    }
    const finder = [true, false, true, true, true, false, true];
    for (const line of lines) {
      let run = 1;
      for (let i = 1; i <= size; i++) {
        if (i < size && line[i] === line[i - 1]) { run++; continue; }
        if (run >= 5) score += 3 + (run - 5);
        run = 1;
      }
      for (let i = 0; i + 7 <= size; i++) {
        if (!finder.every((v, k) => line[i + k] === v)) continue;
        const lightBefore = [1, 2, 3, 4].every((k) => i - k < 0 || !line[i - k]);
        const lightAfter = [0, 1, 2, 3].every((k) => i + 7 + k >= size || !line[i + 7 + k]);
        if (lightBefore || lightAfter) score += 40;
      }
    }
    let dark = 0;
    for (let y = 0; y < size; y++) {
      for (let x = 0; x < size; x++) {
        if (m[y][x]) dark++;
        if (x + 1 < size && y + 1 < size && m[y][x] === m[y][x + 1] && m[y][x] === m[y + 1][x] &&
            m[y][x] === m[y + 1][x + 1]) score += 3;
      }
    }
    score += Math.floor(Math.abs(dark * 20 - size * size * 10) / (size * size)) * 10;
    return score;
  }

  function build(data, version, mask) {
    const grid = makeGrid(version);
    drawFunctionPatterns(grid, version);
    drawCodewords(grid, data);
    applyMask(grid, mask);
    drawFormat(grid, mask);
    return grid.dark;
  }

  BSA.qr = function qr(text, opts) {
    const bytes = toBytes(text);
    const version = pickVersion(bytes.length);
    const data = codewords(bytes, version);
    const want = opts && opts.mask !== undefined && opts.mask !== null ? Number(opts.mask) : null;
    if (want !== null && !(Number.isInteger(want) && want >= 0 && want <= 7)) throw new Error("mask must be 0 to 7");
    let best = null;
    for (let mask = 0; mask < 8; mask++) {
      if (want !== null && mask !== want) continue;
      const modules = build(data, version, mask);
      const score = want !== null ? 0 : penalty(modules);
      if (!best || score < best.score) best = { score, modules };
    }
    return { size: best.modules.length, modules: best.modules };
  };
})();
