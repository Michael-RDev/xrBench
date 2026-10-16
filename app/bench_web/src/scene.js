import * as THREE from 'three';
import {OrbitControls} from 'three/addons/controls/OrbitControls.js';
import {createQuest3,ASSEMBLY_DURATION_MS} from './quest3.js';
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
  const orbit=new THREE.Group(),nodes=[];orbit.name='Benchmark orbit';orbit.position.z=-1.05;scene.add(orbit);
  const nodeGeometry=new THREE.SphereGeometry(.026,10,8);
  for(let i=0;i<25;i++){
    const angle=i/25*Math.PI*2;
    const node=new THREE.Mesh(nodeGeometry,new THREE.MeshBasicMaterial({color:0xbfc8b4}));
    node.position.set(Math.cos(angle)*2.35,-1.14,Math.sin(angle)*2.35);
    orbit.add(node);nodes.push(node);
  }
  const points=Array.from({length:97},(_,i)=>new THREE.Vector3(Math.cos(i/96*Math.PI*2)*2.35,-1.14,Math.sin(i/96*Math.PI*2)*2.35));
  const ring=new THREE.Line(new THREE.BufferGeometry().setFromPoints(points),new THREE.LineBasicMaterial({color:0xdde2d6,transparent:true,opacity:.65}));
  ring.name='Assembly halo';orbit.add(ring);
  const ringColor=new THREE.Color(0xdde2d6),ringPulseColor=new THREE.Color(0x8da88e);
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
  let assembly=null,idleTime=0,positioningCamera=false;
  let homeDistance=8.5;
  const homeDirection=new THREE.Vector3(3.8,2.1,7).normalize();
  const homeSpherical=new THREE.Spherical().setFromVector3(homeDirection),revealSpherical=homeSpherical.clone();
  const cameraOffset=new THREE.Vector3();
  function draw(){if(!disposed&&!lost&&!document.hidden&&current?.visible)renderer.render(scene,camera);}
  function controlChange(){if(!positioningCamera)draw();}
  function updateCamera(){
    // Automatic camera updates share the tick's render rather than drawing twice at 30 FPS.
    positioningCamera=true;controls.update();positioningCamera=false;
  }
  function pose(){headset.position.y=0;headset.rotation.set(0,0,-.045);headset.scale.setScalar(1);}
  function home(){
    camera.position.copy(controls.target).addScaledVector(homeDirection,homeDistance);
    updateCamera();
  }
  function revealCamera(progress){
    if(progress===1){home();return;}
    const remaining=1-progress*progress*(3-2*progress);
    // Forward sensor columns need extra side clearance in narrow canvases until they seat.
    const narrow=THREE.MathUtils.clamp((1.35-camera.aspect)/.35,0,1);
    const framing=THREE.MathUtils.smoothstep(remaining,0,.65)*.12*narrow;
    revealSpherical.set(homeDistance*(1+.2*remaining+framing),homeSpherical.phi,
      homeSpherical.theta+THREE.MathUtils.degToRad(12)*remaining);
    camera.position.copy(controls.target).add(cameraOffset.setFromSpherical(revealSpherical));
    updateCamera();
  }
  function reveal(progress){
    model.setAssemblyProgress(progress);
    const time=progress*ASSEMBLY_DURATION_MS;
    // Fade the composed image smoothly; transparent block batches would show sorting artifacts.
    canvas.style.opacity=String(THREE.MathUtils.smoothstep(time,0,450));
    const pulse=time>2600&&time<ASSEMBLY_DURATION_MS?
      Math.sin(Math.PI*(time-2600)/(ASSEMBLY_DURATION_MS-2600))**2:0;
    ring.material.color.copy(ringColor).lerp(ringPulseColor,pulse);
    ring.material.opacity=.65+.35*pulse;
    ring.scale.set(1+.055*pulse,1,1+.055*pulse);
    headset.position.y=.015*pulse;
    if(!userControlled)revealCamera(progress);
  }
  function resize(){
    if(disposed)return;
    const {width,height}=container.getBoundingClientRect();
    if(!width||!height)return;
    renderer.setSize(width,height,false);camera.aspect=width/height;camera.updateProjectionMatrix();
    homeDistance=8.5*Math.max(1,1.05/camera.aspect);
    controls.minDistance=3.8;controls.maxDistance=homeDistance*1.65;
    if(!userControlled)revealCamera(assembly===null?1:assembly/ASSEMBLY_DURATION_MS);
    draw();
  }
  function animated(){
    return !disposed&&!lost&&!document.hidden&&!reduce.matches&&current?.visible&&
      (assembly!==null || current.authorized&&!userControlled);
  }
  function finishAssembly(){assembly=null;idleTime=0;reveal(1);}
  function tick(time){
    raf=0;
    if(!animated())return;
    frameTime+=Math.max(0,time-last);last=time;
    if(frameTime>=1000/30){
      if(assembly!==null){
        assembly=Math.min(ASSEMBLY_DURATION_MS,assembly+frameTime);
        reveal(assembly/ASSEMBLY_DURATION_MS);
        if(assembly===ASSEMBLY_DURATION_MS){finishAssembly();pose();}
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
    // Assembly continues, but the camera arc and subsequent idle motion relinquish control.
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
  controls.addEventListener('start',interact);controls.addEventListener('change',controlChange);
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
        reveal(reduce.matches?1:0);
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
      controls.removeEventListener('start',interact);controls.removeEventListener('change',controlChange);controls.dispose();
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
