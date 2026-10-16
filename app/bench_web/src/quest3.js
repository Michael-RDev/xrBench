import * as THREE from 'three';
import {RoundedBoxGeometry} from 'three/addons/geometries/RoundedBoxGeometry.js';

// Original procedural exterior, proportioned against Meta's Quest 3 product views.
// +Z is the front; the standard soft strap extends toward -Z. No remote assets.
function outline(t,width,height,nose=0){
  const c=Math.cos(t),s=Math.sin(t);
  const x=width*Math.sign(c)*Math.abs(c)**.63;
  let y=height*Math.sign(s)*Math.abs(s)**.78;
  if(s<0)y+=nose*Math.exp(-((x/.36)**2))*Math.abs(s);
  return new THREE.Vector2(x,y);
}

function shellGeometry(){
  const positions=[],indices=[],segments=96;
  // A softly domed face rolls into the sides, then tapers toward the face interface.
  const rings=[[.001,.60],[.28,.598],[.58,.575],[.82,.53],[.94,.46],
    [1,.32],[1.015,.10],[1.0,-.25],[.94,-.50]];
  for(const [scale,z] of rings){
    for(let i=0;i<=segments;i++){
      const p=outline(i/segments*Math.PI*2,1.67*scale,.81*scale);
      positions.push(p.x,p.y,z-.045*(p.x/1.67)**2);
    }
  }
  for(let j=0;j<rings.length-1;j++)for(let i=0;i<segments;i++){
    const a=j*(segments+1)+i,b=a+segments+1;
    indices.push(a,b,a+1,b,b+1,a+1);
  }
  const geometry=new THREE.BufferGeometry();
  geometry.setAttribute('position',new THREE.Float32BufferAttribute(positions,3));
  geometry.setIndex(indices);geometry.computeVertexNormals();
  return geometry;
}

function faceRing(width,height,band,depth){
  const shape=new THREE.Shape(),hole=new THREE.Path();
  for(let i=0;i<=96;i++){
    const p=outline(i/96*Math.PI*2,width,height,.22);
    const q=outline(-i/96*Math.PI*2,width-band,height-band,.22);
    if(!i){shape.moveTo(p.x,p.y);hole.moveTo(q.x,q.y);}
    else{shape.lineTo(p.x,p.y);hole.lineTo(q.x,q.y);}
  }
  shape.holes.push(hole);
  return new THREE.ExtrudeGeometry(shape,{depth,bevelEnabled:true,bevelSegments:3,
    steps:1,bevelSize:.045,bevelThickness:.045,curveSegments:32});
}

// Sweep a flat, slightly thick ribbon, not a cylindrical tube, along the strap.
function ribbon(points,width,across){
  const curve=new THREE.CatmullRomCurve3(points.map(p=>new THREE.Vector3(...p)));
  const positions=[],indices=[],uvs=[],steps=64,thickness=.035;
  for(let i=0;i<=steps;i++){
    const t=i/steps,p=curve.getPoint(t),tangent=curve.getTangent(t);
    const side=new THREE.Vector3(...across).projectOnPlane(tangent).normalize();
    const normal=new THREE.Vector3().crossVectors(tangent,side).normalize();
    for(const [a,b] of [[-1,-1],[1,-1],[1,1],[-1,1]]){
      const v=p.clone().addScaledVector(side,a*width/2).addScaledVector(normal,b*thickness/2);
      positions.push(v.x,v.y,v.z);
      uvs.push((a+1)/2,t*4);
    }
  }
  for(let i=0;i<steps;i++)for(let edge=0;edge<4;edge++){
    const a=i*4+edge,b=i*4+(edge+1)%4;
    indices.push(a,b,a+4,b,b+4,a+4);
  }
  indices.push(0,2,1,0,3,2,steps*4,steps*4+1,steps*4+2,steps*4,steps*4+2,steps*4+3);
  const geometry=new THREE.BufferGeometry();
  geometry.setAttribute('position',new THREE.Float32BufferAttribute(positions,3));
  geometry.setAttribute('uv',new THREE.Float32BufferAttribute(uvs,2));
  geometry.setIndex(indices);geometry.computeVertexNormals();
  return geometry;
}

function fabricTexture(){
  const size=64,data=new Uint8Array(size*size*4);
  for(let y=0;y<size;y++)for(let x=0;x<size;x++){
    const value=190+((x%4<2)===(y%4<2)?30:0);
    const i=(y*size+x)*4;data[i]=data[i+1]=data[i+2]=value;data[i+3]=255;
  }
  const texture=new THREE.DataTexture(data,size,size);
  texture.wrapS=texture.wrapT=THREE.RepeatWrapping;
  texture.repeat.set(6,2);texture.needsUpdate=true;
  return texture;
}

export function createQuest3(){
  const group=new THREE.Group();group.name='Meta Quest 3';
  const shell=new THREE.MeshStandardMaterial({color:0xf1f1ee,roughness:.42,metalness:.02});
  const seam=new THREE.MeshStandardMaterial({color:0xacaeac,roughness:.7});
  const graphite=new THREE.MeshStandardMaterial({color:0x252729,roughness:.87});
  const foam=new THREE.MeshStandardMaterial({color:0x353638,roughness:1,bumpMap:fabricTexture(),bumpScale:.016});
  const fabric=new THREE.MeshStandardMaterial({color:0xdededa,roughness:1,side:THREE.DoubleSide,
    bumpMap:fabricTexture(),bumpScale:.009});
  const glass=new THREE.MeshPhysicalMaterial({color:0x101319,roughness:.16,metalness:.3,clearcoat:1});
  const optics=new THREE.MeshPhysicalMaterial({color:0x182831,roughness:.12,metalness:.45,clearcoat:1});
  const highlight=new THREE.MeshBasicMaterial({color:0x82969d,transparent:true,opacity:.45});
  function mesh(name,geometry,material,x=0,y=0,z=0,parent=group){
    const object=new THREE.Mesh(geometry,material);object.name=name;
    object.position.set(x,y,z);parent.add(object);return object;
  }
  const box=(name,w,h,d,r,material,x,y,z,parent=group)=>mesh(name,
    new RoundedBoxGeometry(w,h,d,5,r),material,x,y,z,parent);
  // RoundedBoxGeometry clamps its radius to half the depth. Flat sensor windows
  // need an extruded capsule so their end caps stay round even at shallow depth.
  function pill(name,w,h,d,material,x,y,z,parent=group){
    const shape=new THREE.Shape(),r=w/2,straight=h/2-r;
    shape.absarc(0,straight,r,0,Math.PI,false);
    shape.absarc(0,-straight,r,Math.PI,Math.PI*2,false);
    shape.closePath();
    const geometry=new THREE.ExtrudeGeometry(shape,{depth:d,steps:1,curveSegments:20,
      bevelEnabled:true,bevelSize:.006,bevelThickness:.006,bevelSegments:2});
    geometry.translate(0,0,-d/2);
    return mesh(name,geometry,material,x,y,z,parent);
  }
  const disc=(name,r,depth,material,x,y,z,parent=group)=>{
    const object=mesh(name,new THREE.CylinderGeometry(r,r,depth,40),material,x,y,z,parent);
    object.rotation.x=Math.PI/2;return object;
  };
  mesh('Curved front shell',shellGeometry(),shell);
  mesh('Shell seam',faceRing(1.57,.756,.06,.035),seam,0,0,-.52);
  mesh('Facial interface',faceRing(1.56,.755,.12,.22),graphite,0,0,-.77);
  mesh('Fabric facial cushion',faceRing(1.58,.78,.15,.14),foam,0,0,-.94);

  // Two paired outer camera windows; the center has its own depth projector.
  for(const side of [-1,1]){
    const pod=new THREE.Group();pod.position.set(side*1.02,.015,.56);
    pod.rotation.y=side*.10;pod.rotation.z=-side*.045;group.add(pod);
    pill('Outer camera surround',.37,.96,.035,seam,0,0,0,pod);
    pill('Outer camera glass',.305,.87,.025,glass,0,0,.034,pod);
    for(const y of [-.235,.235]){
      disc('Camera bezel',.105,.014,graphite,0,y,.066,pod);
      disc('Camera optic',.074,.017,optics,0,y,.077,pod);
      disc('Camera reflection',.023,.003,highlight,-.019,y+.022,.087,pod);
    }
  }
  pill('Depth sensor surround',.34,.92,.025,seam,0,.015,.596);
  pill('Depth sensor glass',.279,.83,.025,glass,0,.015,.621);
  box('Depth projector',.145,.24,.012,.055,optics,0,.045,.651);
  disc('Depth sensor aperture',.034,.009,graphite,0,-.239,.652);
  for(const side of [-1,1]){
    const tracking=box('Lower tracking camera',.22,.20,.055,.085,glass,side*1.50,-.46,.20);
    tracking.rotation.y=side*.88;
    const arm=box('Speaker arm',.22,.30,1.13,.10,shell,side*1.68,.12,-.72);
    arm.rotation.y=-side*.10;
    const port=box(side<0?'USB-C port':'Headphone port',.025,.075,.19,.025,graphite,side*1.803,.12,-.56);
    port.rotation.x=.05;
    box('Strap attachment',.25,.32,.22,.06,seam,side*1.60,.12,-1.28);
    mesh('Side fabric strap',ribbon([[side*1.62,.12,-1.20],[side*1.63,.12,-2.00],
      [side*1.22,.18,-2.85],[side*.47,.25,-3.12],[0,.28,-3.15]],.29,[0,1,0]),fabric);
    // Split rear band is characteristic of the standard Quest 3 soft strap.
    mesh('Split rear strap',ribbon([[side*1.17,.20,-2.87],[side*.73,.60,-3.05],
      [0,.68,-3.11]],.22,[0,1,0]),fabric);
    box('Strap adjuster',.32,.34,.08,.04,shell,side*.80,.21,-3.06);
  }
  mesh('Top fabric strap',ribbon([[0,.77,-.34],[0,1.22,-1.18],[0,1.24,-2.15],
    [0,.68,-3.11]],.36,[1,0,0]),fabric);
  box('Top strap anchor',.43,.075,.23,.035,shell,0,.79,-.34);

  // Recessed rear optics and nose bridge remain visible when orbiting behind it.
  box('Lens housing',2.81,1.22,.12,.30,graphite,0,.03,-.57);
  for(const side of [-1,1]){
    const bezel=disc('Pancake lens bezel',.49,.09,graphite,side*.70,.055,-.68);
    bezel.scale.x=1.12;
    const lens=disc('Pancake lens',.423,.035,optics,side*.70,.055,-.737);
    lens.scale.x=1.12;
    const ring=mesh('Lens rim',new THREE.TorusGeometry(.446,.018,8,56),seam,side*.70,.055,-.756);
    ring.scale.x=1.12;
    const shine=mesh('Lens soft reflection',new THREE.CircleGeometry(.32,48),highlight,side*.70-.07,.13,-.76);
    shine.rotation.y=Math.PI;shine.scale.set(.62,.16,1);
  }
  box('Proximity sensor',.13,.09,.025,.04,glass,0,.46,-.67);
  box('Power button',.04,.12,.23,.04,seam,-1.70,.14,-.06);
  box('Volume rocker',.40,.05,.13,.04,seam,.81,-.80,-.14);
  const wheel=disc('Lens spacing wheel',.15,.09,graphite,-.65,-.77,-.18);
  wheel.rotation.x=0;wheel.scale.z=.55;
  for(let i=0;i<13;i++)box('Upper ventilation slot',.042,.025,.11,.01,graphite,(i-6)*.10,.801,-.13);
  return group;
}
