/* sp_presence.js — which sample carries which taxon (taxon explorer, round 4).
 *
 * Reads data/taxa/presence/, written by taxon_explorer.py:
 *   index.bin   one container (magic SPXS): per taxon the shard, offset, length and encoding of its block and
 *               its number of carriers; per sample its body site, age group, lifestyle, country and project
 *               (deflated sections); the sample accessions (deflated, newline-separated); the project accessions.
 *   pNNN.bin    shards: the taxon blocks one after another. A block is raw DEFLATE of either the sorted sample
 *               indices as varint gaps (encoding 1) or a bitmap over all samples, most significant bit first
 *               (encoding 2), whichever is smaller.
 * Samples are numbered in the order body site, project, accession, so that carriers form runs.
 *
 * This file only decodes; it fetches nothing. The page fetches index.bin once and one shard per taxon asked for.
 * The DEFLATE decoder is written out here (stored, fixed and dynamic blocks, RFC 1951) so that the page does not
 * depend on DecompressionStream; it is checked against Python's zlib for every block by tests/presence_check.mjs.
 */
(function (root) {
  'use strict';

  /* ------------------------------------------------------------------ raw DEFLATE */
  var LBASE = [3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 15, 17, 19, 23, 27, 31, 35, 43, 51, 59, 67, 83, 99, 115, 131, 163, 195, 227, 258];
  var LEXT = [0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 2, 2, 3, 3, 3, 3, 4, 4, 4, 4, 5, 5, 5, 5, 0];
  var DBASE = [1, 2, 3, 4, 5, 7, 9, 13, 17, 25, 33, 49, 65, 97, 129, 193, 257, 385, 513, 769, 1025, 1537, 2049, 3073,
               4097, 6145, 8193, 12289, 16385, 24577];
  var DEXT = [0, 0, 0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6, 7, 7, 8, 8, 9, 9, 10, 10, 11, 11, 12, 12, 13, 13];
  var CLORD = [16, 17, 18, 0, 8, 7, 9, 6, 10, 5, 11, 4, 12, 3, 13, 2, 14, 1, 15];
  var FIXL = null, FIXD = null;

  function huff(lengths, n) {              /* canonical Huffman table: codes per length, symbols in code order */
    var count = new Uint16Array(16), offs = new Uint16Array(16), symbol = new Uint16Array(n), i;
    for (i = 0; i < n; i++) { count[lengths[i]]++; }
    count[0] = 0;
    for (i = 1; i < 16; i++) { offs[i] = offs[i - 1] + count[i - 1]; }
    for (i = 0; i < n; i++) { if (lengths[i]) { symbol[offs[lengths[i]]++] = i; } }
    return { count: count, symbol: symbol };
  }

  /* src: Uint8Array of raw DEFLATE; maxLen: an upper bound of the decoded length. Returns a Uint8Array. */
  function inflateRaw(src, maxLen) {
    var out = new Uint8Array(maxLen), op = 0, ip = 0, bitbuf = 0, bitcnt = 0;
    function bits(n) {
      while (bitcnt < n) {
        if (ip >= src.length) { throw new Error('sp_presence: input ended early'); }
        bitbuf |= src[ip++] << bitcnt; bitcnt += 8;
      }
      var v = bitbuf & ((1 << n) - 1);
      bitbuf >>>= n; bitcnt -= n;
      return v;
    }
    function decode(t) {
      var code = 0, first = 0, index = 0, len, c;
      for (len = 1; len <= 15; len++) {
        code |= bits(1);
        c = t.count[len];
        if (code - c < first) { return t.symbol[index + (code - first)]; }
        index += c; first += c; first <<= 1; code <<= 1;
      }
      throw new Error('sp_presence: bad Huffman code');
    }
    function codes(lt, dt) {
      for (;;) {
        var sym = decode(lt);
        if (sym < 256) {
          if (op >= maxLen) { throw new Error('sp_presence: output longer than expected'); }
          out[op++] = sym;
        } else if (sym === 256) {
          return;
        } else {
          sym -= 257;
          if (sym >= 29) { throw new Error('sp_presence: bad length code'); }
          var len = LBASE[sym] + bits(LEXT[sym]);
          var ds = decode(dt);
          if (ds >= 30) { throw new Error('sp_presence: bad distance code'); }
          var dist = DBASE[ds] + bits(DEXT[ds]);
          if (dist > op) { throw new Error('sp_presence: distance too far back'); }
          if (op + len > maxLen) { throw new Error('sp_presence: output longer than expected'); }
          for (var k = 0; k < len; k++) { out[op] = out[op - dist]; op++; }
        }
      }
    }
    var last, i;
    do {
      last = bits(1);
      var type = bits(2);
      if (type === 0) {                       /* stored: skip to the byte boundary, then length and its complement */
        bitbuf = 0; bitcnt = 0;
        var slen = src[ip] | (src[ip + 1] << 8);
        ip += 4;
        if (op + slen > maxLen) { throw new Error('sp_presence: output longer than expected'); }
        out.set(src.subarray(ip, ip + slen), op);
        op += slen; ip += slen;
      } else if (type === 1) {
        if (!FIXL) {
          var fl = new Uint8Array(288), fd = new Uint8Array(30);
          for (i = 0; i < 144; i++) { fl[i] = 8; }
          for (; i < 256; i++) { fl[i] = 9; }
          for (; i < 280; i++) { fl[i] = 7; }
          for (; i < 288; i++) { fl[i] = 8; }
          for (i = 0; i < 30; i++) { fd[i] = 5; }
          FIXL = huff(fl, 288); FIXD = huff(fd, 30);
        }
        codes(FIXL, FIXD);
      } else if (type === 2) {
        var nlen = bits(5) + 257, ndist = bits(5) + 1, ncode = bits(4) + 4;
        var cl = new Uint8Array(19), lens = new Uint8Array(nlen + ndist);
        for (i = 0; i < ncode; i++) { cl[CLORD[i]] = bits(3); }
        var ct = huff(cl, 19);
        i = 0;
        while (i < nlen + ndist) {
          var s = decode(ct), rep, val;
          if (s < 16) {
            lens[i++] = s;
          } else {
            if (s === 16) {
              if (i === 0) { throw new Error('sp_presence: repeat with nothing before it'); }
              val = lens[i - 1]; rep = 3 + bits(2);
            } else if (s === 17) { val = 0; rep = 3 + bits(3); } else { val = 0; rep = 11 + bits(7); }
            if (i + rep > nlen + ndist) { throw new Error('sp_presence: too many code lengths'); }
            while (rep--) { lens[i++] = val; }
          }
        }
        codes(huff(lens.subarray(0, nlen), nlen), huff(lens.subarray(nlen), ndist));
      } else {
        throw new Error('sp_presence: bad block type');
      }
    } while (!last);
    return op === maxLen ? out : out.subarray(0, op);
  }

  /* ------------------------------------------------------------------ the index */
  var I = null;

  function bytesOf(buf) {
    if (buf instanceof Uint8Array) { return buf; }
    if (typeof ArrayBuffer !== 'undefined' && buf instanceof ArrayBuffer) { return new Uint8Array(buf); }
    if (buf && buf.buffer) { return new Uint8Array(buf.buffer, buf.byteOffset || 0, buf.byteLength); }
    throw new Error('sp_presence: expected an ArrayBuffer, a typed array or a Node Buffer');
  }
  function ascii(b, from, to) {
    var out = '', i, CH = 8192;
    for (i = from; i < to; i += CH) { out += String.fromCharCode.apply(null, b.subarray(i, Math.min(to, i + CH))); }
    return out;
  }

  function loadIndex(buf) {
    var b = bytesOf(buf);
    if (ascii(b, 0, 4) !== 'SPXS') { throw new Error('sp_presence: not a presence index'); }
    var dv = new DataView(b.buffer, b.byteOffset, b.byteLength);
    var hlen = dv.getUint32(6, true), header = JSON.parse(ascii(b, 10, 10 + hlen)), start = 10 + hlen;
    var copy = new Uint8Array(b.byteLength - start);          /* own, 8-byte aligned copy of the sections */
    copy.set(b.subarray(start));
    var sec = function (name) { var s = header.sections[name]; return copy.subarray(s[0], s[0] + s[1]); };
    var view = function (name, T) { var s = header.sections[name]; return new T(copy.buffer, s[0], s[1] / T.BYTES_PER_ELEMENT); };
    var z = function (name) { return inflateRaw(sec(name), header.raw[name]); };
    var study8 = z('sampStudyZ'), study = new Uint16Array(study8.length / 2), i;
    for (i = 0; i < study.length; i++) { study[i] = study8[2 * i] | (study8[2 * i + 1] << 8); }
    var accOff = view('accOff', Uint32Array), accBlob = sec('accBlob'), accs = [];
    for (i = 0; i + 1 < accOff.length; i++) { accs.push(ascii(accBlob, accOff[i], accOff[i + 1])); }
    I = { header: header, n: header.n_samples, sec: sec,
          taxShard: view('taxShard', Uint16Array), taxOff: view('taxOff', Uint32Array),
          taxLen: view('taxLen', Uint32Array), taxEnc: sec('taxEnc'), taxN: view('taxN', Uint32Array),
          site: z('sampSiteZ'), age: z('sampAgeZ'), life: z('sampLifeZ'), country: z('sampCountryZ'),
          study: study, accs: accs, keys: null };
    return header;
  }

  function keys() {                          /* the sample accessions, decoded on first use (a few megabytes) */
    if (!I.keys) {
      var raw = inflateRaw(I.sec('keysZ'), I.header.raw.keysZ);
      I.keys = ascii(raw, 0, raw.length).split('\n');
    }
    return I.keys;
  }

  /* the sorted sample indices of the taxon's carriers, from the bytes of its shard */
  function carriers(id, shardBytes) {
    var enc = I.taxEnc[id], n = I.taxN[id], out = new Uint32Array(n);
    if (!enc || !n) { return out; }
    var sh = bytesOf(shardBytes), blk = sh.subarray(I.taxOff[id], I.taxOff[id] + I.taxLen[id]);
    var raw, k = 0, i, j;
    if (enc === 1) {
      raw = inflateRaw(blk, 3 * n + 8);
      var prev = -1, v = 0, shift = 0;
      for (i = 0; i < raw.length; i++) {
        v |= (raw[i] & 127) << shift;
        if (raw[i] & 128) { shift += 7; } else { prev += v; out[k++] = prev; v = 0; shift = 0; }
      }
    } else {
      raw = inflateRaw(blk, Math.ceil(I.n / 8));
      for (i = 0; i < raw.length; i++) {
        var byte = raw[i];
        if (!byte) { continue; }
        for (j = 0; j < 8; j++) { if (byte & (128 >> j)) { out[k++] = i * 8 + j; } }
      }
    }
    if (k !== n) { throw new Error('sp_presence: taxon ' + id + ' decoded to ' + k + ' samples, expected ' + n); }
    return out;
  }

  /* keep the carriers that are in a group. crit: {site: key} and optionally one of {age|life|country: key} */
  function filter(idx, crit) {
    var h = I.header, pos = function (list, key) { return list.indexOf(key); };
    var s = crit.site !== undefined && crit.site !== null ? pos(h.site_keys, crit.site) : -1;
    var arr = null, want = -1;
    if (crit.age !== undefined) { arr = I.age; want = pos(h.age_keys, crit.age); }
    else if (crit.life !== undefined) { arr = I.life; want = pos(h.life_keys, crit.life); }
    else if (crit.country !== undefined) { arr = I.country; want = pos(h.country_keys, crit.country); }
    if ((crit.site !== undefined && crit.site !== null && s < 0) || (arr && want < 0)) { return new Uint32Array(0); }
    var out = new Uint32Array(idx.length), k = 0, i, x;
    for (i = 0; i < idx.length; i++) {
      x = idx[i];
      if (s >= 0 && I.site[x] !== s) { continue; }
      if (arr && arr[x] !== want) { continue; }
      out[k++] = x;
    }
    return out.subarray(0, k);
  }

  function sample(i) {
    var h = I.header, c = I.country[i];
    return { index: i, key: keys()[i], project: I.accs[I.study[i]], site: h.site_keys[I.site[i]],
             age: h.age_keys[I.age[i]], life: h.life_keys[I.life[i]], country: c === 255 ? null : h.country_keys[c] };
  }

  root.SPP = {
    inflateRaw: inflateRaw, loadIndex: loadIndex, carriers: carriers, filter: filter, sample: sample, keys: keys,
    ready: function () { return !!I; },
    header: function () { return I ? I.header : null; },
    nSamples: function () { return I ? I.n : 0; },
    count: function (id) { return I ? I.taxN[id] : 0; },
    studyIndex: function (i) { return I.study[i]; },
    shardOf: function (id) { return I && I.taxEnc[id] ? I.taxShard[id] : -1; },
    shardName: function (k) { return 'p' + ('00' + k).slice(-3) + '.bin'; }
  };
})(typeof window !== 'undefined' ? window : globalThis);
