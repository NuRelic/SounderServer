// Music ducking in the browser: does the duck ALWAYS release?
//
// A clip fired over a song used to play at the song's own level (one shared MASTER gain,
// no song/clip distinction at all), so it was masked by the music — which is why this
// presented as "the shorter sounds don't play" while every clip was in fact playing.
// Songs now ride a duckable SONGBUS and each clip holds the duck down while it sounds.
//
// The failure mode that matters is a STUCK duck: if a hold is ever taken and not released,
// the music stays quiet forever with nothing to reset it but a reload. So these exercise
// every release path, plus overlap (two clips must not release each other's hold).
//
// No test framework and no dependencies — the duck code is pulled straight out of
// templates/index.html so this tests the shipped source rather than a copy of it:
//     node tests/duck_bookkeeping.test.js
const fs = require('fs');
const src = fs.readFileSync(require('path').join(__dirname, '..', 'templates', 'index.html'),'utf8');

// pull the REAL duck code out of the template so this tests shipped code, not a copy
function grab(sig){
  const i = src.indexOf(sig);
  if(i < 0) throw new Error('could not find '+sig+' in templates/index.html — if the duck code was renamed or moved, update this extractor');
  let d=0, j=src.indexOf('{', i);
  for(let k=j;k<src.length;k++){ if(src[k]==='{')d++; else if(src[k]==='}'){d--; if(!d) return src.slice(i,k+1);} }
}
const DUCK = parseFloat(/const SONG_DUCK = ([0-9.]+)/.exec(src)[1]);
const code = [
  'const SONG_DUCK = ' + /const SONG_DUCK = ([0-9.]+)/.exec(src)[1] + ';',
  'let _duckN = 0;',
  grab('function applyDuck()'),
  grab('function duckHold(a)'),
  'globalThis.__duckN = () => _duckN;',
].join('\n');

// minimal stubs for what the duck code touches
let AC = {currentTime:0}, SONGBUS = {gain:{_v:1, setTargetAtTime(v){this._v=v;}}};
const playing = {}, _wired = new WeakSet(), _songEls = new WeakSet();
const effVol = () => 1;

eval(code);

class FakeAudio {                       // just the EventTarget bits duckHold uses
  constructor(){ this.h = {}; }
  addEventListener(ev, fn){ (this.h[ev] ||= []).push(fn); }
  emit(ev){ (this.h[ev]||[]).forEach(f=>f()); }
}
const gain = () => SONGBUS.gain._v;
let fails = 0;
function check(name, cond){ console.log((cond?'  ok  ':'  FAIL')+'  '+name); if(!cond) fails++; }

console.log('duck bookkeeping:');
let a = new FakeAudio(); duckHold(a);
check('idle → music at full', gain() === 1);
a.emit('playing');  check('clip sounding → music ducked', gain() === DUCK);
a.emit('ended');    check('clip ended → music restored', gain() === 1);

a = new FakeAudio(); duckHold(a);
a.emit('playing'); a.emit('playing');            // duplicate events
a.emit('ended');
check('duplicate playing events do not pin the duck', gain() === 1 && __duckN() === 0);

a = new FakeAudio(); duckHold(a);
a.emit('playing'); a.emit('ended'); a.emit('pause');   // ended THEN pause (real browsers do this)
check('ended followed by pause does not double-release', gain() === 1 && __duckN() === 0);

const b = new FakeAudio(), c = new FakeAudio(); duckHold(b); duckHold(c);
b.emit('playing'); c.emit('playing');
check('two overlapping clips → still ducked', gain() === DUCK);
b.emit('ended');
check('one of two ends → STILL ducked', gain() === DUCK);
c.emit('ended');
check('both end → restored', gain() === 1 && __duckN() === 0);

a = new FakeAudio(); duckHold(a);
a.emit('playing'); a.emit('pause');
check('a clip stopped mid-play releases the music', gain() === 1);
a = new FakeAudio(); duckHold(a);
a.emit('error');
check('a clip that failed never ducks', gain() === 1 && __duckN() === 0);
a = new FakeAudio(); duckHold(a);
a.emit('playing'); a.emit('error');
check('a clip erroring mid-play releases', gain() === 1 && __duckN() === 0);

console.log(fails ? '\n'+fails+' FAILED' : '\nall duck bookkeeping checks passed');
process.exit(fails?1:0);
