import * as THREE from 'three';
import {RoundedBoxGeometry} from 'three/addons/geometries/RoundedBoxGeometry.js';

export function createScene(container){
  const renderer=new THREE.WebGLRenderer({alpha:true,antialias:true,powerPreference:'low-power'});
  renderer.setPixelRatio(Math.min(devicePixelRatio,1.5));renderer.setClearColor(0xffffff,0);
  renderer.outputColorSpace=THREE.SRGBColorSpace;
  const scene=new THREE.Scene(),camera=new THREE.PerspectiveCamera(35,1,.1,100);
  camera.position.set(4,2.7,7);camera.lookAt(0,0,0);
  scene.add(new THREE.HemisphereLight(0xffffff,0xa6b5a0,3));
  const key=new THREE.DirectionalLight(0xffffff,4);key.position.set(-3,5,6);scene.add(key);
  const fill=new THREE.DirectionalLight(0xd8e2c9,2);fill.position.set(4,1,-3);scene.add(fill);
  const headset=new THREE.Group();headset.rotation.z=-.13;scene.add(headset);
  const shell=new THREE.MeshStandardMaterial({color:0xe7e9df,roughness:.31,metalness:.08});
  const rim=new THREE.MeshStandardMaterial({color:0x4a5149,roughness:.9});
  const glass=new THREE.MeshStandardMaterial({color:0x161e1c,roughness:.16,metalness:.55});
  const mesh=(geo,mat,x=0,y=0,z=0,parent=headset)=>{const object=new THREE.Mesh(geo,mat);object.position.set(x,y,z);parent.add(object);return object;};
  mesh(new RoundedBoxGeometry(3.05,1.5,.8,4,.35),shell);
  mesh(new RoundedBoxGeometry(2.97,1.43,.38,4,.3),rim,0,0,-.39);
  for(const x of [-.9,0,.9]){
    mesh(new RoundedBoxGeometry(.32,.86,.1,4,.16),glass,x,.01,.43);
    for(const y of [-.2,.21]){
      mesh(new THREE.CylinderGeometry(.085,.085,.035,24),new THREE.MeshStandardMaterial({color:0x324442,metalness:.65,roughness:.14}),x,y,.495).rotation.x=Math.PI/2;
      mesh(new THREE.SphereGeometry(.022,12,8),new THREE.MeshBasicMaterial({color:0x9baea8}),x-.025,y+.024,.518);
    }
  }
  for(const side of [-1,1]){
    mesh(new RoundedBoxGeometry(.28,.38,.85,3,.11),shell,side*1.51,.15,-.35);
    const curve=new THREE.CatmullRomCurve3([new THREE.Vector3(side*1.5,.15,-.3),new THREE.Vector3(side*1.56,.15,-1.35),new THREE.Vector3(side*.85,.15,-2.07),new THREE.Vector3(0,.15,-2.2)]);
    mesh(new THREE.TubeGeometry(curve,40,.105,8,false),shell);
  }
  const arch=new THREE.CatmullRomCurve3([new THREE.Vector3(0,.62,-.35),new THREE.Vector3(0,1.2,-1.2),new THREE.Vector3(0,.15,-2.2)]);
  mesh(new THREE.TubeGeometry(arch,40,.11,8,false),shell);
  const nodes=[],orbit=new THREE.Group();scene.add(orbit);
  for(let i=0;i<25;i++){
    const angle=(i/25)*Math.PI*2;
    const m=mesh(new THREE.SphereGeometry(.035,12,8),new THREE.MeshBasicMaterial({color:0xbfc8b4}),Math.cos(angle)*2.4,-1.25,Math.sin(angle)*2.4,orbit);nodes.push(m);
  }
  const points=Array.from({length:81},(_,i)=>new THREE.Vector3(Math.cos(i/80*Math.PI*2)*2.4,-1.25,Math.sin(i/80*Math.PI*2)*2.4));
  orbit.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(points),new THREE.LineBasicMaterial({color:0xdde2d6,transparent:true,opacity:.7})));
  container.append(renderer.domElement);container.querySelector('#scene-fallback').hidden=true;
  const reduce=matchMedia('(prefers-reduced-motion: reduce)');
  let raf=0,last=0,lost=false;
  const draw=()=>{if(!lost)renderer.render(scene,camera);};
  const resize=()=>{const {width,height}=container.getBoundingClientRect();renderer.setSize(width,height,false);camera.aspect=width/height;camera.updateProjectionMatrix();draw();};
  const observer=new ResizeObserver(resize);observer.observe(container);
  function tick(time){raf=0;if(document.hidden||reduce.matches||lost)return;if(time-last>40){headset.position.y=Math.sin(time*.00035)*.035;headset.rotation.y=Math.sin(time*.00022)*.08;draw();last=time;}raf=requestAnimationFrame(tick);}
  function motion(){cancelAnimationFrame(raf);if(!document.hidden&&!reduce.matches&&!lost)raf=requestAnimationFrame(tick);else draw();}
  reduce.addEventListener('change',motion);document.addEventListener('visibilitychange',motion);
  renderer.domElement.addEventListener('webglcontextlost',event=>{event.preventDefault();lost=true;cancelAnimationFrame(raf);renderer.domElement.hidden=true;container.querySelector('#scene-fallback').hidden=false;});
  renderer.domElement.addEventListener('webglcontextrestored',()=>{lost=false;renderer.domElement.hidden=false;container.querySelector('#scene-fallback').hidden=true;resize();motion();});
  resize();motion();
  return {update(states){const colors={passed:0x397458,attention:0xb08b4f,running:0x71895f,pending:0xbfc8b4};nodes.forEach((node,i)=>node.material.color.setHex(colors[states[i]] || colors.pending));draw();}};
}
