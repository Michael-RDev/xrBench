import {test} from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFile} from 'node:fs/promises';
import {JSDOM} from 'jsdom';
import * as THREE from 'three';
import {OrbitControls} from 'three/addons/controls/OrbitControls.js';
import {createQuest3} from '../src/quest3.js';
import {deviceView,deviceTransition} from '../web/device-view.mjs';

const ready=deviceView({devices:[{serial:'quest',model:'Quest 3',state:'device',supported:true}]});
async function viewer(reduced=false){
  const dom=new JSDOM('<div id="scene"><div id="scene-fallback"></div></div>',{pretendToBeVisual:true});
  const {window}=dom,document=window.document,container=document.querySelector('#scene');
  container.getBoundingClientRect=()=>({width:540,height:400});
  let time=0,hidden=false,sequence=0,renderer,observer;
  const frames=new Map(),availability=[];
  Object.defineProperty(document,'hidden',{get:()=>hidden});
  const media=new window.EventTarget();media.matches=reduced;
  class Renderer{
    constructor(){
      renderer=this;this.renders=0;this.disposed=false;this.domElement=document.createElement('canvas');
      Object.defineProperty(this.domElement,'clientHeight',{value:400});
      this.domElement.setPointerCapture=()=>{};this.domElement.releasePointerCapture=()=>{};
    }
    setPixelRatio(){} setClearColor(){} setSize(){}
    render(scene,camera){this.renders++;this.scene=scene;this.camera=camera;}
    dispose(){this.disposed=true;}
  }
  const context=vm.createContext({document,devicePixelRatio:1,performance:{now:()=>time},
    matchMedia:()=>media,requestAnimationFrame:callback=>{const id=++sequence;frames.set(id,callback);return id;},
    cancelAnimationFrame:id=>frames.delete(id),ResizeObserver:class{
      constructor(callback){observer=this;this.callback=callback;}observe(){}disconnect(){this.disconnected=true;}
    }});
  const sources={three:{...THREE,WebGLRenderer:Renderer},
    'three/addons/controls/OrbitControls.js':{OrbitControls},'./quest3.js':{createQuest3},
    '../web/device-view.mjs':{deviceTransition}};
  const module=new vm.SourceTextModule(await readFile(new URL('../src/scene.js',import.meta.url),'utf8'),{context});
  await module.link(specifier=>new vm.SyntheticModule(Object.keys(sources[specifier]),function(){
    for(const [key,value] of Object.entries(sources[specifier]))this.setExport(key,value);
  },{context}));
  await module.evaluate();
  const scene=module.namespace.createScene(container,{onAvailabilityChange:value=>availability.push(value)});
  const key=value=>renderer.domElement.dispatchEvent(new window.KeyboardEvent('keydown',{key:value,bubbles:true,cancelable:true}));
  const pointer=(type,id,x,y,pointerType='touch')=>{
    const event=new window.Event(type,{bubbles:true,cancelable:true});
    Object.assign(event,{pointerId:id,pointerType,button:0,clientX:x,clientY:y,pageX:x,pageY:y});
    (type==='pointerdown'?renderer.domElement:document).dispatchEvent(event);
  };
  return {scene,renderer,media,frames,availability,key,pointer,window,observer,
    advance(ms){time+=ms;const callbacks=[...frames.values()];frames.clear();for(const callback of callbacks)callback(time);},
    hide(value){hidden=value;document.dispatchEvent(new window.Event('visibilitychange'));},
    close(){scene.dispose();window.close();}};
}

test('orbit input preserves camera across polls, supports touch/pinch, zoom limits and reset',async()=>{
  const v=await viewer();
  try{
    v.scene.setDevice(ready);v.advance(1100);
    const camera=v.renderer.camera,home=camera.position.clone(),target=new THREE.Vector3(0,0,-1.05);
    v.key('ArrowRight');const rotated=camera.position.clone();assert.ok(rotated.distanceTo(home)>.1);
    assert.equal(v.frames.size,0);
    v.scene.setDevice(structuredClone(ready));assert.ok(camera.position.distanceTo(rotated)<1e-10);
    assert.equal(v.frames.size,0);
    for(let i=0;i<50;i++)v.key('+');assert.ok(Math.abs(camera.position.distanceTo(target)-3.8)<1e-8);
    for(let i=0;i<60;i++)v.key('-');assert.ok(Math.abs(camera.position.distanceTo(target)-8.5*1.65)<1e-8);
    v.key('Home');assert.ok(camera.position.distanceTo(home)<1e-8);
    assert.equal(v.frames.size,1);
    v.pointer('pointerdown',1,150,200);v.pointer('pointermove',1,200,180);v.pointer('pointerup',1,200,180);
    assert.ok(camera.position.distanceTo(home)>.1);assert.equal(v.frames.size,0);
    const before=camera.position.distanceTo(target);
    v.pointer('pointerdown',2,150,200);v.pointer('pointerdown',3,250,200);
    v.pointer('pointermove',3,330,200);v.pointer('pointerup',2,150,200);v.pointer('pointerup',3,330,200);
    assert.ok(camera.position.distanceTo(target)<before);
    v.scene.resetView();assert.ok(camera.position.distanceTo(home)<1e-8);
  }finally{v.close();}
});

test('reduced motion, hidden pages, unplugging and context loss suspend automatic rendering',async()=>{
  const v=await viewer(true);
  try{
    v.scene.setDevice(ready);assert.equal(v.frames.size,0);
    const before=v.renderer.renders;v.key('ArrowLeft');assert.ok(v.renderer.renders>before);
    v.media.matches=false;v.media.dispatchEvent(new v.window.Event('change'));
    assert.equal(v.frames.size,0); // Interaction stays in control when preferences change.
    v.scene.resetView();assert.equal(v.frames.size,1);
    v.hide(true);assert.equal(v.frames.size,0);
    const hiddenCount=v.renderer.renders;v.scene.update(['passed']);assert.equal(v.renderer.renders,hiddenCount);
    v.hide(false);assert.equal(v.frames.size,1);
    const lost=new v.window.Event('webglcontextlost',{cancelable:true});
    v.renderer.domElement.dispatchEvent(lost);
    assert.equal(lost.defaultPrevented,true);assert.equal(v.frames.size,0);
    assert.equal(v.availability.at(-1),false);
    v.renderer.domElement.dispatchEvent(new v.window.Event('webglcontextrestored'));
    assert.equal(v.availability.at(-1),true);assert.equal(v.frames.size,1);
    v.scene.setDevice(deviceView({devices:[]}));assert.equal(v.frames.size,0);
    v.scene.setDevice(ready);assert.equal(v.frames.size,1);
    v.media.matches=true;v.media.dispatchEvent(new v.window.Event('change'));
    assert.equal(v.frames.size,0);
  }finally{v.close();}
  assert.equal(v.renderer.disposed,true);assert.equal(v.observer.disconnected,true);
  assert.equal(v.frames.size,0);
});

test('the full model has finite geometry and cleanup disposes shared GPU resources once',async()=>{
  const v=await viewer();
  v.scene.setDevice(ready);
  const resources=new Map();
  v.renderer.scene.traverse(object=>{
    if(object.geometry){
      for(const n of object.geometry.attributes.position.array)assert.ok(Number.isFinite(n));
      resources.set(object.geometry,0);
    }
    if(object.material){resources.set(object.material,0);if(object.material.bumpMap)resources.set(object.material.bumpMap,0);}
  });
  for(const resource of resources.keys())resource.addEventListener('dispose',()=>resources.set(resource,resources.get(resource)+1));
  v.scene.dispose();v.scene.dispose();
  assert.ok(resources.size>20);
  assert.ok([...resources.values()].every(count=>count===1));
  v.close();
});
