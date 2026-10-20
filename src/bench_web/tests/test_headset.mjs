import {test} from 'node:test';
import assert from 'node:assert/strict';
import {JSDOM} from 'jsdom';
import {avoidanceOffset,createHeadsetMesh,createAsciiRenderer,mountHeadset} from '../web/ascii-headset.mjs';

const silhouette = text => text.replace(/[^ \n]/g, 'x');
function uncut(text, columns, rows) {
  const lines = text.split('\n');
  assert.equal(lines.length, rows); assert.ok(lines.every(line => line.length === columns));
  assert.ok(lines[0].trim() === '' && lines.at(-1).trim() === '');
  assert.ok(lines.every(line => line[0] === ' ' && line.at(-1) === ' '));
  assert.ok(text.trim().length > 0);
}

test('3D headset changes silhouette with orientation and fits desktop and phone grids', () => {
  const renderer = createAsciiRenderer(createHeadsetMesh()); renderer.resize(160, 53);
  const front = renderer.render({yaw: 0}), angled = renderer.render({yaw: -.65}), back = renderer.render({yaw: 2.6});
  assert.notEqual(silhouette(front), silhouette(angled)); assert.notEqual(silhouette(angled), silhouette(back));
  assert.ok(new Set(angled.replace(/[ \n]/g, '')).size >= 6, 'surfaces have multiple shading levels');
  for (const [columns, rows] of [[100,44], [128,50], [160,53]]) for (const cellAspect of [.45, .5, .6]) {
    renderer.resize(columns, rows);
    for (const yaw of [-.95, -.55, -.15, 0, 2.6]) uncut(renderer.render({yaw, cellAspect}), columns, rows);
  }
});

test('denser artwork fills the hero without clipping and preserves the open strap silhouette', () => {
  const renderer = createAsciiRenderer();
  for (const [columns,rows] of [[100,44],[160,53]]) {
    renderer.resize(columns,rows);
    const text=renderer.render(), lines=text.split('\n');
    const occupied=lines.flatMap((line,y)=>[...line].flatMap((char,x)=>char===' '?[]:[{x,y}]));
    const width=Math.max(...occupied.map(p=>p.x))-Math.min(...occupied.map(p=>p.x))+1;
    const height=Math.max(...occupied.map(p=>p.y))-Math.min(...occupied.map(p=>p.y))+1;
    assert.ok(width/columns>=.74,'the headset uses at least three quarters of the character grid width');
    assert.ok(height/rows>=.85,'the larger headset fills the grid height');
    assert.ok(occupied.length>columns*rows*.35,'higher density retains substantial surface detail');
    // The crown and rear band leave open air above the solid visor.
    assert.ok(lines.slice(4,Math.floor(rows*.4)).some(line=>/\S +\S/.test(line)));
    uncut(text,columns,rows);
  }
});

test('depth buffer hides rear geometry regardless of triangle submission order', () => {
  const triangle = (z, albedo) => [-1,-.8,z,0,0,1,albedo, 1,-.8,z,0,0,1,albedo, 0,.8,z,0,0,1,albedo];
  const near = triangle(1, .95), far = triangle(-1, .05);
  function draw(vertices, triangles) {
    const renderer = createAsciiRenderer({vertices: new Float32Array(vertices), triangles: new Uint32Array(triangles)});
    renderer.resize(72,30); return renderer.render({yaw:0,pitch:0,roll:0});
  }
  const expected = draw(near,[0,1,2]);
  assert.equal(draw([...near,...far],[0,1,2,3,4,5]), expected);
  assert.equal(draw([...near,...far],[3,4,5,0,1,2]), expected);
  assert.notEqual(draw(far,[0,1,2]), expected);
});

test('pointer repulsion stays within the stage and retreats from each direction', () => {
  const bounds = {left:0,top:0,width:500,height:350}, art = {width:390,height:220};
  for (const x of [0,125,250,375,500]) for (const y of [0,100,175,250,350]) {
    const offset = avoidanceOffset(x,y,bounds,art);
    assert.ok(Math.abs(offset.x)<=43); assert.ok(Math.abs(offset.y)<=32);
    if(x<250)assert.ok(offset.x>=0); if(x>250)assert.ok(offset.x<=0);
    if(y<175)assert.ok(offset.y>=0); if(y>175)assert.ok(offset.y<=0);
  }
  assert.deepEqual(avoidanceOffset(0,0,{left:0,top:0,width:100,height:100},art),{x:0,y:0});
});

function page(createRenderer) {
  const dom = new JSDOM('<style>pre{line-height:1.2;margin:0}</style><div id="stage"><pre></pre><section id="chip-dashboard">Live measurements</section></div>', {pretendToBeVisual:true});
  const win=dom.window, doc=win.document, stage=doc.querySelector('#stage'), art=stage.querySelector('pre');
  let width=500,height=350,hidden=false,time=0,id=0,writes=0;
  const frames=new Map(),queries=new Map(),observers={};
  Object.defineProperty(doc,'hidden',{get:()=>hidden});
  stage.getBoundingClientRect=()=>({left:0,top:0,width:stage.hidden?0:width,height:stage.hidden?0:height});
  art.getBoundingClientRect=()=>{
    const lines=art.textContent.split('\n'),size=parseFloat(art.style.fontSize)||7;
    return {width:Math.max(...lines.map(line=>line.length))*size*.6,height:lines.length*size*1.2};
  };
  const text=Object.getOwnPropertyDescriptor(win.Node.prototype,'textContent');
  Object.defineProperty(art,'textContent',{get(){return text.get.call(this);},set(value){writes++;text.set.call(this,value);}});
  win.matchMedia=query=>{
    const media={matches:query.includes('hover'),listeners:new Set(),addEventListener(_,fn){this.listeners.add(fn);},removeEventListener(_,fn){this.listeners.delete(fn);}};
    queries.set(query,media); return media;
  };
  for(const name of ['IntersectionObserver','ResizeObserver'])win[name]=class {
    constructor(callback){this.callback=callback;this.disconnected=false;observers[name]=this;}
    observe(){} disconnect(){this.disconnected=true;}
  };
  win.requestAnimationFrame=fn=>{frames.set(++id,fn);return id;};win.cancelAnimationFrame=id=>frames.delete(id);
  const component=mountHeadset(stage,win,createRenderer);
  return {win,doc,stage,art,component,frames,queries,observers,get writes(){return writes;},
    advance(ms){const end=time+ms;while(time<end){time=Math.min(end,time+1000/60);for(const [id,fn]of [...frames]){frames.delete(id);fn(time);}}},
    pointer(type='mouse'){const e=new win.MouseEvent('pointermove',{clientX:290,clientY:175});Object.defineProperty(e,'pointerType',{value:type});stage.dispatchEvent(e);},
    preference(key,value){const media=[...queries.entries()].find(([query])=>query.includes(key))[1];media.matches=value;for(const fn of media.listeners)fn();},
    visible(value){observers.IntersectionObserver.callback([{target:stage,isIntersecting:value}]);},
    hidden(value){hidden=value;doc.dispatchEvent(new win.Event('visibilitychange'));},
    resize(w,h){width=w;height=h;observers.ResizeObserver.callback();},
    close(){component.destroy();dom.window.close();},
  };
}

test('one throttled idle loop rotates the model and combines with bounded cursor avoidance', () => {
  const p=page();
  try {
    const before=p.art.textContent, writes=p.writes;
    for(let i=0;i<20;i++)p.component.setPresentation({mode:'headset'});
    assert.equal(p.frames.size,1);assert.equal(p.writes,writes);
    p.advance(1000);assert.ok(p.writes-writes<=30);assert.notEqual(p.art.textContent,before);
    p.pointer();p.advance(500);assert.match(p.art.style.transform,/translate3d\(-/);
    p.stage.dispatchEvent(new p.win.Event('pointerleave'));p.advance(1000);assert.equal(p.art.style.transform,'');
    p.pointer('touch');p.advance(500);assert.equal(p.art.style.transform,'');
    assert.equal(p.frames.size,1);
  } finally {p.close();}
});

test('reduced motion and touch-only settings use the same static three-quarter pose', () => {
  const p=page();
  try {
    const rest=p.art.textContent;p.advance(1200);p.pointer();p.advance(300);
    p.preference('reduced-motion',true);assert.equal(p.frames.size,0);assert.equal(p.art.style.transform,'');assert.equal(p.art.textContent,rest);
    const writes=p.writes;p.pointer();p.advance(1000);assert.equal(p.writes,writes);
    p.preference('hover',false);p.preference('reduced-motion',false);assert.equal(p.frames.size,0);assert.equal(p.art.textContent,rest);
    p.preference('hover',true);assert.equal(p.frames.size,1);
  } finally {p.close();}
});

test('offscreen, hidden-tab and settled chip states pause rendering; resize resumes without clipping', () => {
  const p=page();
  try {
    for(const [pause,resume] of [[()=>p.visible(false),()=>p.visible(true)],[()=>p.hidden(true),()=>p.hidden(false)],
      [()=>p.component.setPresentation({mode:'chip',runKey:'restored'}),()=>p.component.setPresentation({mode:'headset'})]]) {
      p.advance(100);pause();const writes=p.writes;assert.equal(p.frames.size,0);
      p.advance(10000);assert.equal(p.writes,writes);resume();assert.equal(p.frames.size,1);
    }
    p.resize(265,275);uncut(p.art.textContent,100,52);assert.ok(p.art.getBoundingClientRect().width<265);
    p.resize(579,430);uncut(p.art.textContent,160,58);assert.ok(p.art.getBoundingClientRect().height<430);
    p.component.destroy();const writes=p.writes;p.pointer();p.advance(1000);p.doc.dispatchEvent(new p.win.Event('visibilitychange'));
    assert.equal(p.frames.size,0);assert.equal(p.writes,writes);
    assert.ok([...p.queries.values()].every(media=>media.listeners.size===0));
    assert.ok(Object.values(p.observers).every(observer=>observer.disconnected));
  } finally {p.close();}
});


test('Run separates the headset, flies the chip forward, and anchors the diagnostic face',()=>{
  const renderer=createAsciiRenderer();renderer.resize(160,80);
  const stages=[0,.3,.65,1].map(reveal=>renderer.render({reveal,faceColumns:100,chipRatio:1}));
  assert.equal(new Set(stages.map(silhouette)).size,4);
  const face=renderer.chipFace;
  assert.ok(Math.abs(face.width-100)<.001);assert.ok(face.left>0&&face.left+face.width<160);
  assert.ok(face.top>0&&face.top+face.height<80);
  assert.notEqual(renderer.render({reveal:1,processor:'cpu'}),renderer.render({reveal:1,processor:'gpu'}));
});

test('reveal plays once per run, stays bounded, and repeated progress updates preserve its clock',()=>{
  const p=page();
  try{
    p.component.setPresentation({mode:'chip',runKey:'first',animate:true});
    const first=p.art.textContent;p.advance(500);
    assert.notEqual(p.art.textContent,first);assert.equal(p.stage.dataset.transition,'opening');
    const writes=p.writes;
    for(let i=0;i<20;i++)p.component.setPresentation({mode:'chip',runKey:'first',animate:true});
    assert.equal(p.writes,writes);assert.equal(p.frames.size,1);
    p.advance(1200);assert.equal(p.stage.dataset.transition,'settled');assert.equal(p.frames.size,0);
    assert.equal(p.stage.querySelector('#chip-dashboard').style.opacity,'1');
    p.pointer();p.advance(500);assert.equal(p.art.style.transform,'');
    p.component.setPresentation({mode:'chip',runKey:'second',animate:true});assert.equal(p.stage.dataset.transition,'opening');
    p.component.setPresentation({mode:'headset'});assert.equal(p.stage.dataset.mode,'headset');assert.equal(p.frames.size,1);
  }finally{p.close();}
});

test('reduced motion skips reveal; touch permits Run animation but has no idle or repulsion',()=>{
  const p=page();
  try{
    p.preference('reduced-motion',true);
    p.component.setPresentation({mode:'chip',runKey:'reduced',animate:true});
    assert.equal(p.stage.dataset.transition,'settled');assert.equal(p.frames.size,0);
    p.preference('hover',false);p.preference('reduced-motion',false);
    p.component.setPresentation({mode:'headset'});assert.equal(p.frames.size,0);
    p.component.setPresentation({mode:'chip',runKey:'touch',animate:true});assert.equal(p.frames.size,1);
    p.advance(500);p.hidden(true);const text=p.art.textContent;p.advance(4000);assert.equal(p.art.textContent,text);
    p.hidden(false);p.advance(1200);assert.equal(p.frames.size,0);assert.equal(p.stage.dataset.transition,'settled');
  }finally{p.close();}
});

test('renderer failure leaves readable diagnostics and does not start an animation loop',()=>{
  const p=page(()=>{throw Error('renderer unavailable');});
  try{
    p.component.setPresentation({mode:'chip',runKey:'fallback',animate:true});
    assert.equal(p.stage.dataset.graphics,'fallback');assert.equal(p.frames.size,0);
    assert.match(p.stage.textContent,/Live measurements/);assert.equal(p.stage.hasAttribute('aria-hidden'),false);
  }finally{p.close();}
});
