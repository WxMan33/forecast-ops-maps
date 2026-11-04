/* Model guidance history and timing. No weights, alerts or forecast calculations are changed.
   Accumulations are preceding-hour amounts, labelled by their ending UTC instant. */
(function(root){'use strict';
const H=3600000,finite=x=>typeof x==='number'&&Number.isFinite(x),same=(a,b)=>JSON.stringify(a)===JSON.stringify(b);
const quantile=(a,q)=>{if(!a.length)return null;const s=a.slice().sort((x,y)=>x-y);return s[Math.floor((s.length-1)*q)];};
function hours(ms,s,e){return ms.map((t,i)=>({t,i})).filter(x=>x.t>s&&x.t<=e);}
function oneTiming(ms,a,s,e,threshold=.01){
 const ix=hours(ms,s,e),expected=(e-s)/H;
 if(!Number.isInteger(expected)||ix.length!==expected||ix.some((x,j)=>x.t!==s+(j+1)*H||!finite(a?.[x.i])))return {status:'limited',expected,covered:ix.filter(x=>finite(a?.[x.i])).length};
 const wet=ix.filter(x=>a[x.i]>=threshold),rounds=[];let segment=null;
 wet.forEach(x=>{if(segment&&segment.end===x.t-H){segment.end=x.t;segment.amount+=a[x.i];}else{segment={start:x.t-H,end:x.t,amount:a[x.i]};rounds.push(segment);}});
 const peak=wet.length?wet.reduce((a0,b)=>a[a0.i]>=a[b.i]?a0:b):null;
 return {status:wet.length?'wet':'quiet',rounds,onset:wet.length?wet[0].t-H:null,end:wet.length?wet.at(-1).t:null,onsetClipped:wet.length?wet[0].t-H===s:false,endClipped:wet.length?wet.at(-1).t===e:false,peak:peak?peak.t-H:null,total:ix.reduce((v,x)=>v+a[x.i],0)};
}
function timing(md,ms,s,e,threshold=.01){
 if(md.kind==='nws')return {status:'intervals',reason:'NWS amounts arrive as multi-hour intervals, so an hourly onset is not inferred.'};
 if(md.kind!=='ens')return oneTiming(ms,md.h?.precipitation,s,e,threshold);
 const all=md.members||[],usable=all.map((m,i)=>({i,t:oneTiming(ms,m.h?.precipitation,s,e,threshold)})).filter(x=>x.t.status!=='limited');
 if(!usable.length)return {status:'limited',members:0,of:all.length};
 const wet=usable.filter(x=>x.t.status==='wet');return {status:wet.length?'ensemble':'quiet',members:usable.length,of:all.length,memberIds:usable.map(x=>x.i),wet:wet.length,onset:quantile(wet.map(x=>x.t.onset),.5),end:quantile(wet.map(x=>x.t.end),.5),onsetRange:[quantile(wet.map(x=>x.t.onset),.1),quantile(wet.map(x=>x.t.onset),.9)],endRange:[quantile(wet.map(x=>x.t.end),.1),quantile(wet.map(x=>x.t.end),.9)],onsetClipped:wet.some(x=>x.t.onsetClipped),endClipped:wet.some(x=>x.t.endClipped),partial:usable.length<all.length};
}
function capture(C,data,loc,at=Date.now()){
 const groups=C.time.dayGroups(data.times),snap={version:1,method:'model-trends-v1',at,tz:data.tz,loc:[+loc.lat.toFixed(4),+loc.lon.toFixed(4)],models:{}};
 for(const [id,md] of Object.entries(data.models)){
  const rows=C.blend.dailyRows(data,{[id]:1}),by={};
  for(const r of rows){const d=groups.find(x=>x.k===r.k);if(!d?.idx.length)continue;const first=data.ms[d.idx[0]],last=data.ms[d.idx.at(-1)];
   const start=C.time.toUtc(r.k+'T00:00',data.tz),next=new Date(Date.parse(r.k+'T00:00:00Z')+86400000).toISOString().slice(0,10),end=C.time.toUtc(next+'T00:00',data.tz);
   if(first!==start||last!==end-H||d.idx.length!==(end-start)/H||d.idx.some((i,j)=>data.ms[i]!==start+j*H))continue; // no shrinking/partial calendar-day comparisons
   const support={};for(const [key,hv] of [['hi','temperature_2m'],['lo','temperature_2m'],['qpf','precipitation'],['sn','snowfall'],['gust','wind_gusts_10m']])support[key]=md.kind==='ens'?(md.members||[]).map((m,i)=>C.stats.complete(m.h[hv],d.idx)?i:null).filter(x=>x!=null):[id];
   by[r.k]={win:[start,end],lo:r.lo,hi:r.hi,qpf:r.qpf,sn:r.sn,gust:r.gust,support,prorated:!!r.prorated,qpfBounds:r.qpfBounds||null,timing:timing(md,data.ms,start,end)};
  }
  const confirmed=md.provenance?.provider==='NOAA native GRIB2'?md.provenance.cycle:null;
  snap.models[id]={kind:md.kind||'det',confirmedCycle:confirmed,retrievedAt:md.retrievedAt||null,rows:by};
 }
 return snap;
}
function signature(s){return JSON.stringify({method:s.method,models:Object.fromEntries(Object.entries(s.models).map(([id,m])=>[id,{kind:m.kind,cycle:m.confirmedCycle,rows:m.rows}]))});}
function history(previous,current){const a=(Array.isArray(previous)?previous:[]).filter(x=>x.version===1&&x.method===current.method&&same(x.loc,current.loc)&&x.tz===current.tz).slice(-6);if(a.length&&signature(a.at(-1))===signature(current))return {items:a,current:a.at(-1),changed:false};return {items:a.concat(current).slice(-6),current,changed:true};}
function compare(current,previous){const changes=[],coverage=[],fields={lo:3,hi:3,qpf:.1,sn:.5,gust:8};if(!previous||current.method!==previous.method||current.tz!==previous.tz||!same(current.loc,previous.loc))return {changes,coverage};
 for(const [id,m] of Object.entries(current.models)){const old=previous.models[id];if(!old)continue;for(const [day,r] of Object.entries(m.rows)){const p=old.rows[day];if(!p||!same(r.win,p.win)||m.kind!==old.kind)continue;
  for(const [field,min] of Object.entries(fields)){if(!finite(r[field])||!finite(p[field]))continue;const changedSupport=!same(r.support[field],p.support[field]);if(changedSupport||(field==='qpf'&&(r.prorated||p.prorated))){if(Math.abs(r[field]-p[field])>=min)coverage.push({id,day,field,reason:changedSupport?'member coverage changed':'NWS interval timing limits the comparison'});continue;}
   if(Math.abs(r[field]-p[field])>=min)changes.push({id,day,field,from:p[field],to:r[field],delta:r[field]-p[field],window:r.win});
  }
  const a=p.timing,b=r.timing;
  const bounded=t=>t&&['quiet','wet'].includes(t.status)&&!t.members;
  if(bounded(a)&&bounded(b)&&a.status!==b.status)changes.push({id,day,field:'rainSignal',from:a.status==='wet',to:b.status==='wet',delta:b.status==='wet'?1:-1,window:r.win});
  if(a?.members&&b?.members&&!a.partial&&!b.partial&&same(a.memberIds,b.memberIds)&&Math.abs(b.wet/b.members-a.wet/a.members)>=.2)changes.push({id,day,field:'wetMembers',from:100*a.wet/a.members,to:100*b.wet/b.members,delta:100*(b.wet/b.members-a.wet/a.members),window:r.win});
  const valid=t=>t&&['wet','ensemble'].includes(t.status)&&(!t.members||!t.partial);
  if(valid(a)&&valid(b)&&same(a.memberIds,b.memberIds))for(const [field,clip] of [['onset','onsetClipped'],['end','endClipped']])if(!a[clip]&&!b[clip]&&finite(a[field])&&finite(b[field])&&Math.abs(b[field]-a[field])>=2*H)changes.push({id,day,field,from:a[field],to:b[field],delta:(b[field]-a[field])/H,window:r.win});
 }
 }return {changes,coverage};
}
const api={H,oneTiming,timing,capture,history,compare,signature};if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.ModelTrends=api;
})(typeof globalThis!=='undefined'?globalThis:this);
