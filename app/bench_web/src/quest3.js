import * as THREE from 'three';
import {RoundedBoxGeometry} from 'three/addons/geometries/RoundedBoxGeometry.js';

// Original, stylized Quest 3 exterior. +Z faces forward; the soft strap extends -Z.
// Only the shell's surface cells are retained. Every block shares one bevel geometry.
const PITCH=.175;
const random=index=>{const n=Math.sin(index*127.1+311.7)*43758.5453;return n-Math.floor(n);};
const clamp=value=>THREE.MathUtils.clamp(value,0,1);

export function createQuest3(){
  const group=new THREE.Group();group.name='Meta Quest 3';
  const geometry=new RoundedBoxGeometry(1,1,1,1,.10);
  const styles={
    shell:{name:'Ivory shell blocks',color:0xe8e9e2,roughness:.5,phase:0},
    cushion:{name:'Graphite cushion blocks',color:0x343a37,roughness:.95,phase:.16},
    sensor:{name:'Sensor and lens blocks',color:0x172327,roughness:.24,metalness:.22,phase:.28},
    strap:{name:'Soft strap blocks',color:0xd8dcd2,roughness:.9,phase:.38},
  };
  const blocks=Object.fromEntries(Object.keys(styles).map(key=>[key,[]]));
  function block(kind,x,y,z,size=PITCH*.94,rotation=new THREE.Quaternion(),color=null){
    blocks[kind].push({end:new THREE.Vector3(x,y,z),
      size:new THREE.Vector3(...(Array.isArray(size)?size:[size,size,size])),
      rotation:rotation.clone(),color});
  }

  // A rounded voxel volume, open at the rear for the face cushion and optics.
  const cells=new Set(),key=(i,j,k)=>`${i},${j},${k}`;
  for(let i=-10;i<10;i++)for(let j=-4;j<=4;j++)for(let k=-2;k<=3;k++){
    const x=(i+.5)*PITCH,y=j*PITCH,z=k*PITCH;
    if((x/1.73)**4+(y/.83)**4+((z-.03)/.64)**4<=1)cells.add(key(i,j,k));
  }
  for(const cell of cells){
    const [i,j,k]=cell.split(',').map(Number);
    const rim=[[1,0],[-1,0],[0,1],[0,-1]].some(([dx,dy])=>!cells.has(key(i+dx,j+dy,k)));
    const front=!cells.has(key(i,j,k+1));
    if(!rim&&!front)continue;
    const x=(i+.5)*PITCH,y=j*PITCH,z=k*PITCH;
    const sensor=front&&k>=2&&Math.abs(y)<.46&&[-1.05,0,1.05].some(cx=>Math.abs(x-cx)<.12);
    block(sensor?'sensor':'shell',x,y,z+(sensor ? .025 : 0),PITCH*(sensor ? .91 : .94),
      undefined,sensor&&Math.abs(j)===1?0x283c43:null);
  }

  // Two rows of dark foam frame the open rear; blue-black tiled optics sit inside.
  for(let i=-9;i<9;i++)for(let j=-4;j<=4;j++){
    const x=(i+.5)*PITCH,y=j*PITCH;
    if((x/1.62)**4+(y/.80)**4>1||(x/1.39)**4+(y/.57)**4<1)continue;
    for(const z of [-.51,-.68])block('cushion',x,y,z);
  }
  for(const side of [-1,1]){
    for(let i=-3;i<=3;i++)for(let j=-3;j<=3;j++){
      const radius=Math.hypot(i*.14,j*.14);
      if(radius>.47)continue;
      block(radius>.36?'cushion':'sensor',side*.70+i*.14,.04+j*.14,-.65,.132,
        undefined,radius<=.36?0x243740:null);
    }
    for(let k=0;k<6;k++)for(let j=0;j<2;j++){
      block('shell',side*1.66,.04+j*.16,-.42-k*.16,.151);
    }
  }

  // Cubes follow the flat soft-strap bands, with a consistent gap and shared bevel.
  function strap(points,width,across){
    const curve=new THREE.CatmullRomCurve3(points.map(p=>new THREE.Vector3(...p)));
    const steps=Math.round(curve.getLength()/.165);
    for(let i=0;i<=steps;i++){
      const t=i/steps,center=curve.getPointAt(t),tangent=curve.getTangentAt(t).normalize();
      const side=new THREE.Vector3(...across).projectOnPlane(tangent).normalize();
      const normal=new THREE.Vector3().crossVectors(tangent,side).normalize();
      const rotation=new THREE.Quaternion().setFromRotationMatrix(new THREE.Matrix4().makeBasis(side,normal,tangent));
      for(let j=0;j<width;j++){
        const position=center.clone().addScaledVector(side,(j-(width-1)/2)*.165);
        block('strap',...position.toArray(),[.154,.115,.154],rotation);
      }
    }
  }
  for(const side of [-1,1]){
    strap([[side*1.64,.12,-1.20],[side*1.63,.14,-1.95],
      [side*1.19,.22,-2.81],[side*.45,.28,-3.10],[0,.30,-3.13]],2,[0,1,0]);
    strap([[side*1.16,.23,-2.83],[side*.72,.59,-3.04],[0,.68,-3.10]],1,[0,1,0]);
  }
  strap([[0,.75,-.36],[0,1.20,-1.18],[0,1.23,-2.13],[0,.68,-3.10]],2,[1,0,0]);

  let index=0;
  const batches=[];
  for(const [kind,entries] of Object.entries(blocks)){
    const style=styles[kind];
    const material=new THREE.MeshStandardMaterial({roughness:style.roughness,metalness:style.metalness||0});
    const mesh=new THREE.InstancedMesh(geometry,material,entries.length);mesh.name=style.name;
    mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
    // Four batches are cheaper to draw than to recompute their changing bounds.
    mesh.frustumCulled=false;group.add(mesh);
    for(let i=0;i<entries.length;i++){
      const entry=entries[i],seed=++index;
      const r=random(seed),s=random(seed+1200),u=random(seed+2400);
      entry.start=entry.end.clone().multiplyScalar(1.04).add(new THREE.Vector3(
        (r-.5)*.55,.22+(s-.5)*.85,(u-.5)*.50));
      entry.control=entry.start.clone().lerp(entry.end,.52).add(new THREE.Vector3(
        (s-.5)*.34,.18,(r-.5)*.25));
      entry.spin=new THREE.Quaternion().setFromEuler(new THREE.Euler((r-.5)*1.7,(s-.5)*1.7,(u-.5)*1.7))
        .multiply(entry.rotation);
      entry.delay=style.phase+clamp((entry.end.x+1.8)/3.6)*.11+r*.12;
      entry.duration=.32+s*.04;
      mesh.setColorAt(i,new THREE.Color(entry.color??style.color).multiplyScalar(.96+r*.07));
    }
    batches.push({mesh,entries});
  }
  const position=new THREE.Vector3(),rotation=new THREE.Quaternion(),scale=new THREE.Vector3();
  const matrix=new THREE.Matrix4();
  let previous=-1;
  function setAssemblyProgress(progress){
    const p=clamp(Number.isFinite(progress)?progress:1);
    if(p===previous)return;
    previous=p;
    for(const {mesh,entries} of batches){
      for(let i=0;i<entries.length;i++){
        const entry=entries[i],t=clamp((p-entry.delay)/entry.duration);
        const ease=t*t*(3-2*t),inverse=1-ease;
        position.copy(entry.start).multiplyScalar(inverse*inverse)
          .addScaledVector(entry.control,2*inverse*ease).addScaledVector(entry.end,ease*ease);
        rotation.slerpQuaternions(entry.spin,entry.rotation,ease);
        scale.copy(entry.size).multiplyScalar(.38+.62*ease+.035*Math.sin(Math.PI*t));
        matrix.compose(position,rotation,scale);mesh.setMatrixAt(i,matrix);
      }
      mesh.instanceMatrix.needsUpdate=true;
    }
  }
  setAssemblyProgress(1);
  return {group,setAssemblyProgress};
}
