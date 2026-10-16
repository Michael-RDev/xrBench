import * as THREE from 'three';
import {OrbitControls} from 'three/addons/controls/OrbitControls.js';
import {createQuest3} from './quest3.js';
import {deviceTransition} from '../web/device-view.mjs';

export function createScene(container,{onAvailabilityChange=()=>{}}={}){
  const renderer=new THREE.WebGLRenderer({alpha:true,antialias:true,powerPreference:'low-power'});
  renderer.setPixelRatio(Math.min(devicePixelRatio,1.5));
  renderer.setClearColor(0xffffff,0);
  renderer.outputColorSpace=THREE.SRGBColorSpace;
  renderer.toneMapping=THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure=1.15;
  const scene=new THREE.Scene(),camera=new THREE.PerspectiveCamera(35,1,.1,60);
  scene.add(new THREE.HemisphereLight(0xffffff,0x92979c,2.1));
  const key=new THREE.DirectionalLight(0xfffaf4,3.2);key.position.set(-3,6,5);scene.add(key);
  const fill=new THREE.DirectionalLight(0xdfeaff,1.6);fill.position.set(4,2,-3);scene.add(fill);
  const model=createQuest3(),headset=model.group;scene.add(headset);
  const orbit=new THREE.Group(),nodes=[];orbit.position.z=-1.05;scene.add(orbit);
  const nodeGeometry=new THREE.SphereGeometry(.026,10,8);
  for(let i=0;i<25;i++){
    const angle=i/25*Math.PI*2;
    const node=new THREE.Mesh(nodeGeometry,new THREE.MeshBasicMaterial({color:0xbfc8b4}));
    node.position.set(Math.cos(angle)*2.35,-1.14,Math.sin(angle)*2.35);
    orbit.add(node);nodes.push(node);
  }
  const points=Array.from({length:97},(_,i)=>new THREE.Vector3(Math.cos(i/96*Math.PI*2)*2.35,-1.14,Math.sin(i/96*Math.PI*2)*2.35));
  orbit.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(points),new THREE.LineBasicMaterial({color:0xdde2d6,transparent:true,opacity:.65})));
  const canvas=renderer.domElement,fallback=container.querySelector('#scene-fallback');
  canvas.tabIndex=0;canvas.setAttribute('role','img');
  canvas.setAttribute('aria-label','Interactive block-built 3D model of Meta Quest 3');
  canvas.setAttribute('aria-describedby','viewer-hint');
  container.append(canvas);fallback.hidden=true;
  const controls=new OrbitControls(camera,canvas);
  controls.enablePan=false;controls.enableDamping=false;controls.rotateSpeed=.65;
  controls.zoomSpeed=.75;controls.minPolarAngle=.08;controls.maxPolarAngle=Math.PI-.08;
  controls.target.set(0,0,-1.05);
  controls.touches.TWO=THREE.TOUCH.DOLLY_ROTATE;
  const reduce=matchMedia('(prefers-reduced-motion: reduce)');
  let raf=0,last=0,frameTime=0,lost=false,disposed=false,current=null,userControlled=false;
  let assembly=null,idleTime=0;
  let homeDistance=8.5;
  const homeDirection=new THREE.Vector3(3.8,2.1,7).normalize();
  function draw(){if(!disposed&&!lost&&!document.hidden&&current?.visible)renderer.render(scene,camera);}
  function pose(){headset.position.y=0;headset.rotation.set(0,0,-.045);headset.scale.setScalar(1);}
  function home(){
    camera.position.copy(controls.target).addScaledVector(homeDirection,homeDistance);
    controls.update();
  }
  function resize(){
    if(disposed)return;
    const {width,height}=container.getBoundingClientRect();
    if(!width||!height)return;
    renderer.setSize(width,height,false);camera.aspect=width/height;camera.updateProjectionMatrix();
    homeDistance=8.5*Math.max(1,1.05/camera.aspect);
    controls.minDistance=3.8;controls.maxDistance=homeDistance*1.65;
    if(!userControlled)home();
    draw();
  }
  function animated(){
    return !disposed&&!lost&&!document.hidden&&!reduce.matches&&current?.visible&&
      (assembly!==null || current.authorized&&!userControlled);
  }
  function finishAssembly(){assembly=null;idleTime=0;model.setAssemblyProgress(1);}
  function tick(time){
    raf=0;
    if(!animated())return;
    frameTime+=Math.max(0,time-last);last=time;
    if(frameTime>=1000/30){
      if(assembly!==null){
        assembly=Math.min(2500,assembly+frameTime);
        const progress=assembly/2500;
        model.setAssemblyProgress(progress);
        // A small final lift settles to the same pose from which idle starts.
        headset.position.y=.025*Math.sin(Math.PI*THREE.MathUtils.clamp((progress-.88)/.12,0,1));
        if(assembly===2500){finishAssembly();pose();}
      }else{
        idleTime+=frameTime;
        headset.position.y=Math.sin(idleTime*.0007)*.025;
        headset.rotation.y=Math.sin(idleTime*.00035)*.025;
      }
      draw();frameTime=0;
    }
    if(animated())raf=requestAnimationFrame(tick);
  }
  function motion(){
    if(reduce.matches){finishAssembly();pose();}
    if(animated()){
      if(!raf){last=performance.now();frameTime=0;raf=requestAnimationFrame(tick);}
    }else{cancelAnimationFrame(raf);raf=0;frameTime=0;}
    draw();
  }
  function interact(){
    userControlled=true;
    // Assembly continues under the user's camera; only idle motion is paused.
    motion();
  }
  function resetView(){
    if(!current?.visible||disposed)return;
    userControlled=false;finishAssembly();pose();home();motion();
  }
  function keydown(event){
    if(!current?.visible||lost||event.altKey||event.ctrlKey||event.metaKey)return;
    if(!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown','+','=','-','_','Home'].includes(event.key))return;
    event.preventDefault();
    if(event.key==='Home'){resetView();return;}
    interact();
    const offset=camera.position.clone().sub(controls.target),spherical=new THREE.Spherical().setFromVector3(offset);
    if(event.key==='ArrowLeft')spherical.theta-=.12;
    if(event.key==='ArrowRight')spherical.theta+=.12;
    if(event.key==='ArrowUp')spherical.phi-=.12;
    if(event.key==='ArrowDown')spherical.phi+=.12;
    if(['+','='].includes(event.key))spherical.radius*=.9;
    if(['-','_'].includes(event.key))spherical.radius/=.9;
    spherical.phi=THREE.MathUtils.clamp(spherical.phi,controls.minPolarAngle,controls.maxPolarAngle);
    spherical.radius=THREE.MathUtils.clamp(spherical.radius,controls.minDistance,controls.maxDistance);
    camera.position.copy(controls.target).add(offset.setFromSpherical(spherical));controls.update();draw();
  }
  function contextLost(event){
    event.preventDefault();lost=true;controls.enabled=false;cancelAnimationFrame(raf);raf=0;
    canvas.hidden=true;fallback.hidden=false;onAvailabilityChange(false);
  }
  function contextRestored(){
    lost=false;controls.enabled=!!current?.visible;canvas.hidden=false;fallback.hidden=true;
    resize();motion();onAvailabilityChange(true);
  }
  const observer=new ResizeObserver(resize);observer.observe(container);
  controls.addEventListener('start',interact);controls.addEventListener('change',draw);
  canvas.addEventListener('keydown',keydown);
  canvas.addEventListener('webglcontextlost',contextLost);
  canvas.addEventListener('webglcontextrestored',contextRestored);
  reduce.addEventListener('change',motion);document.addEventListener('visibilitychange',motion);
  home();pose();resize();
  return {
    setDevice(view){
      if(disposed)return;
      const transition=deviceTransition(current,view),wasVisible=!!current?.visible;
      current=view;headset.visible=orbit.visible=view.visible;controls.enabled=view.visible&&!lost;
      if(transition.identityChanged || !wasVisible&&view.visible){userControlled=false;finishAssembly();pose();home();}
      if(transition.enter){
        userControlled=false;pose();idleTime=0;assembly=reduce.matches?null:0;
        model.setAssemblyProgress(reduce.matches?1:0);
      }
      if(!view.visible){finishAssembly();pose();}
      if(view.visible&&!wasVisible)resize();
      motion();
    },
    update(states){
      if(disposed)return;
      const colors={passed:0x397458,attention:0xb08b4f,running:0x71895f,pending:0xbfc8b4};
      nodes.forEach((node,i)=>node.material.color.setHex(colors[states[i]] || colors.pending));draw();
    },
    resetView,
    dispose(){
      if(disposed)return;
      disposed=true;cancelAnimationFrame(raf);observer.disconnect();
      reduce.removeEventListener('change',motion);document.removeEventListener('visibilitychange',motion);
      controls.removeEventListener('start',interact);controls.removeEventListener('change',draw);controls.dispose();
      canvas.removeEventListener('keydown',keydown);canvas.removeEventListener('webglcontextlost',contextLost);
      canvas.removeEventListener('webglcontextrestored',contextRestored);
      const resources=new Set();
      scene.traverse(object=>{
        if(object.isInstancedMesh)resources.add(object);
        if(object.geometry)resources.add(object.geometry);
        for(const material of object.material?(Array.isArray(object.material)?object.material:[object.material]):[]){
          resources.add(material);
          for(const value of Object.values(material))if(value?.isTexture)resources.add(value);
        }
      });
      for(const resource of resources)resource.dispose();
      renderer.dispose();canvas.remove();fallback.hidden=false;
    },
  };
}
