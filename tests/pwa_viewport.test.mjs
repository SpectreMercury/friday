import test from 'node:test';
import assert from 'node:assert/strict';
import { appViewport, trackAppViewport } from '../local_ai/pwa/core.js';

const iphone = { standalone: true, ios: true, width: 393, height: 793,
  screenWidth: 393, screenHeight: 852, visualHeight: 793 };

test('installed PWA releases JS height so native fixed edges include safe areas once', () => {
  for (const visualHeight of [759, 793, 852]) {
    assert.deepEqual(appViewport({ ...iphone, visualHeight }), { height: null, top: 0, keyboard: false });
  }
  // Ignore residual scroll offsets once the keyboard has closed.
  assert.deepEqual(appViewport({ ...iphone, visualTop: 24 }), { height: null, top: 0, keyboard: false });
});

test('all visible viewport changes constrain the shell, not only keyboard-size changes', () => {
  for (const visualHeight of [460, 740, 793]) {
    const layout = appViewport({ ...iphone, standalone: false, visualHeight, visualTop: 24 });
    assert.equal(layout.top + layout.height, 24 + visualHeight);
  }
  assert.deepEqual(appViewport({ height: 372, visualHeight: 351 }), { height: 351, top: 0, keyboard: false });
});

test('focusout cannot expand the shell before the keyboard actually finishes closing', () => {
  for (const editing of [true, false])
    assert.deepEqual(appViewport({ ...iphone, editing, visualHeight: 460, visualTop: 36 }),
      { height: 460, top: 36, keyboard: true });
  assert.deepEqual(appViewport({ ...iphone, editing: false }), { height: null, top: 0, keyboard: false });
});

test('fallback handles missing viewport metrics and ignores pinch zoom', () => {
  assert.deepEqual(appViewport({ height: 667 }), { height: 667, top: 0, keyboard: false });
  assert.deepEqual(appViewport({ height: 667, visualHeight: 0, visualTop: 100 }), { height: 667, top: 0, keyboard: false });
  assert.equal(appViewport({ height: 0 }), null);
  assert.equal(appViewport({ ...iphone, scale: 2 }), null);
});

test('tracker follows keyboard, blur, dismissal, rotation and resume, then removes listeners', () => {
  const props = new Map(), classes = new Map(), raf = new Map();
  const win = Object.assign(new EventTarget(), { innerHeight: 793, navigator: { standalone: true },
    matchMedia: () => Object.assign(new EventTarget(), { matches: true }),
    visualViewport: Object.assign(new EventTarget(), { height: 793, offsetTop: 0, scale: 1 }) });
  win.requestAnimationFrame = fn => { raf.set(1, fn); return 1; }; win.cancelAnimationFrame = id => raf.delete(id);
  const doc = Object.assign(new EventTarget(), { documentElement: {
    style: { removeProperty: key => props.delete(key), setProperty: (key, value) => props.set(key, value) }, classList: { toggle: (key, value) => classes.set(key, value) },
  } });
  const flush = () => { for (const fn of raf.values()) fn(); raf.clear(); };
  const stop = trackAppViewport(win, doc);
  assert.equal(props.has('--app-height'), false); assert.equal(props.has('--app-bottom'), false);
  win.visualViewport.height = 460; win.visualViewport.offsetTop = 36;
  win.visualViewport.dispatchEvent(new Event('resize')); flush();
  assert.equal(props.get('--app-height'), '460px'); assert.equal(props.get('--app-top'), '36px');
  assert.equal(classes.get('keyboard-open'), true);
  doc.dispatchEvent(new Event('focusout')); flush();
  assert.equal(props.get('--app-height'), '460px'); assert.equal(classes.get('keyboard-open'), true);
  win.visualViewport.height = 793; win.visualViewport.offsetTop = 0;
  win.visualViewport.dispatchEvent(new Event('resize')); flush();
  assert.equal(props.has('--app-height'), false); assert.equal(props.has('--app-bottom'), false); assert.equal(classes.get('keyboard-open'), false);
  win.innerHeight = win.visualViewport.height = 372;
  win.dispatchEvent(new Event('orientationchange')); flush();
  assert.equal(props.has('--app-height'), false);
  win.innerHeight = win.visualViewport.height = 793; win.dispatchEvent(new Event('pageshow')); flush();
  assert.equal(props.has('--app-height'), false); assert.equal(props.has('--app-bottom'), false);
  win.visualViewport.scale = 2; win.visualViewport.height = 396;
  win.visualViewport.dispatchEvent(new Event('resize')); flush();
  assert.equal(props.has('--app-height'), false); assert.equal(props.has('--app-bottom'), false);
  stop(); win.dispatchEvent(new Event('resize')); assert.equal(raf.size, 0);
});
