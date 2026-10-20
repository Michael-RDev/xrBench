const runPattern=/^run-[0-9a-f]{32}$/;
export function selectedRunId(search=location.search){
  const id=new URLSearchParams(search).get('run');
  return runPattern.test(id || '')?id:null;
}
export function frontendLink(path,id){return path+(runPattern.test(id || '')?'?run='+id:'');}
export function rememberRun(id){
  if(!runPattern.test(id || ''))return;
  const url=new URL(location.href);
  if(url.searchParams.get('run')===id)return;
  url.searchParams.set('run',id);
  history.replaceState(null,'',url);
}
