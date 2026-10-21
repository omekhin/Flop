/* Общие данные: оценка рук и диапазоны. Скопировано из play.html — менять в обоих местах. */
'use strict';
/* ================= cards & evaluation ================= */
const RANKS='23456789TJQKA', SUITS='cdhs', SUIT_SYM={c:'♣',d:'♦',h:'♥',s:'♠'};
let BB=2, SB=1, START=200, MODE='cash';
const LEVELS=[[25,50],[50,100],[75,150],[100,200],[150,300],[200,400],[300,600],[400,800],[600,1200],[800,1600],[1000,2000],[1500,3000],[2000,4000],[3000,6000],[4000,8000],[6000,12000],[8000,16000],[10000,20000]];
const T={every:10,start:5000,level:0,hands:0};
const M=v=>MODE==='tour'?Math.round(v).toLocaleString('ru-RU'):'$'+v;
const cardStr=c=>RANKS[c>>2]+SUITS[c&3];
const rnd=Math.random;
function straightHigh(m){ for(let h=12;h>=4;h--){ if(((m>>(h-4))&31)===31) return h; } if((m&0b1000000001111)===0b1000000001111) return 3; return -1; }
function topBits(m,n,excl){ const out=[]; for(let r=12;r>=0&&out.length<n;r--) if((m>>r)&1 && !(excl&&excl.includes(r))) out.push(r); return out; }
function kick(list){ let v=0; for(let i=0;i<5;i++) v=v*13+(list[i]??0); return v; }
function evalHand(cs){
  const rc=new Array(13).fill(0), sc=[0,0,0,0], sm=[0,0,0,0]; let rm=0;
  for(const c of cs){ const r=c>>2,s=c&3; rc[r]++; sc[s]++; sm[s]|=1<<r; rm|=1<<r; }
  let fs=-1; for(let s=0;s<4;s++) if(sc[s]>=5) fs=s;
  if(fs>=0){ const sf=straightHigh(sm[fs]); if(sf>=0) return 8e6+sf; }
  let quad=-1; const trips=[],pairs=[];
  for(let r=12;r>=0;r--){ if(rc[r]===4) quad=r; else if(rc[r]===3) trips.push(r); else if(rc[r]===2) pairs.push(r); }
  if(quad>=0){ const k=topBits(rm,1,[quad]); return 7e6+quad*13+(k[0]??0); }
  if(trips.length && (trips.length>1||pairs.length)){ const p=Math.max(trips[1]??-1,pairs[0]??-1); return 6e6+trips[0]*13+p; }
  if(fs>=0) return 5e6+kick(topBits(sm[fs],5));
  const st=straightHigh(rm); if(st>=0) return 4e6+st;
  if(trips.length){ const k=topBits(rm,2,[trips[0]]); return 3e6+trips[0]*169+k[0]*13+(k[1]??0); }
  if(pairs.length>=2){ const k=topBits(rm,1,[pairs[0],pairs[1]]); return 2e6+pairs[0]*169+pairs[1]*13+(k[0]??0); }
  if(pairs.length===1){ const k=topBits(rm,3,[pairs[0]]); return 1e6+pairs[0]*2197+(k[0]??0)*169+(k[1]??0)*13+(k[2]??0); }
  return kick(topBits(rm,5));
}
const CAT_RU=['старшая карта','пара','две пары','сет / трипс','стрит','флеш','фулл-хаус','каре','стрит-флеш'];
const CAT_EN=['high card','a pair','two pair','three of a kind','a straight','a flush','a full house','four of a kind','a straight flush'];
function chen(a,b){
  const r1=a>>2,r2=b>>2,hi=Math.max(r1,r2),lo=Math.min(r1,r2);
  const val=r=>r===12?10:r===11?8:r===10?7:r===9?6:(r+2)/2;
  let s=val(hi);
  if(hi===lo) s=Math.max(5,s*2);
  else{ if((a&3)===(b&3)) s+=2; const g=hi-lo-1; s-= g===0?0:g===1?1:g===2?2:g===3?4:5; if(g<=1&&hi<10) s+=1; }
  return Math.ceil(s);
}
function handClass(a,b){
  let r1=a>>2,r2=b>>2; if(r1<r2){const t=r1;r1=r2;r2=t;}
  if(r1===r2) return RANKS[r1]+RANKS[r2];
  return RANKS[r1]+RANKS[r2]+(((a&3)===(b&3))?'s':'o');
}
const ORD='AKQJT98765432';
function expand(spec){
  const out=new Set();
  for(let t of spec.split(',')){ t=t.trim(); if(!t) continue; const plus=t.endsWith('+'); t=t.replace('+','');
    if(t.length===2 && t[0]===t[1]){ const i=ORD.indexOf(t[0]); for(const r of (plus?ORD.slice(0,i+1):t[0])) out.add(r+r); }
    else{ const a=t[0],b=t[1],s=t[2]; const ia=ORD.indexOf(a), ib=ORD.indexOf(b);
      if(plus){ for(let j=ia+1;j<=ib;j++) out.add(a+ORD[j]+s); } else out.add(t); } }
  return out;
}
const RNG={
  EP:expand('66+,ATs+,KJs+,QJs,JTs,AJo+,KQo'),
  MP:expand('44+,A8s+,KTs+,QTs+,JTs,T9s,ATo+,KJo+'),
  LATE:expand('22+,A2s+,K9s+,Q9s+,J9s+,T9s,98s,87s,76s,A9o+,KTo+,QTo+,JTo'),
  BTN:expand('22+,A2s+,K5s+,Q8s+,J8s+,T8s+,97s+,86s+,76s,65s,A5o+,K9o+,Q9o+,J9o+,T9o'),
  SBSTEAL:expand('22+,A2s+,K2s+,Q5s+,J7s+,T7s+,97s+,86s+,75s+,65s,54s,A2o+,K8o+,Q9o+,J9o+,T9o'),
  SBCOMPLETE:expand('22+,A2s+,K9s+,Q9s+,J9s+,T8s+,97s+,86s+,75s+,65s,54s'),
  BBRAISE:expand('TT+,AQs+,AQo+,KQs'),
  THREE_EP:expand('QQ+,AKs,AKo'),
  THREE_LATE:expand('JJ+,AQs+,AQo+'),
  CALL_EP:expand('JJ,TT,99,88,77,66,55,44,33,22,AQs'),
  CALL_MP:expand('JJ,TT,99,88,77,66,55,44,33,22,AQs,AJs,KQs,AQo'),
  CALL_LATE:expand('TT,99,88,77,66,55,44,33,22,AJs,ATs,KQs,KJs,QJs,JTs,T9s,AQo'),
  BBDEF:expand('22+,A2s+,A9o+,K9s+,KTo+,Q9s+,QTo+,J9s+,JTo,T8s+,97s+,86s+,76s,65s'),
  SBCALL:expand('99,88,77,66,55,44,33,22'),
  CONT4:expand('QQ+,AKs,AKo'),
  CONT4W:expand('TT+,AQs+,AQo+'),
  HU_SB:expand('22+,A2s+,A2o+,K2s+,K2o+,Q2s+,Q4o+,J3s+,J6o+,T5s+,T7o+,95s+,97o+,85s+,87o,74s+,76o,64s+,53s+,43s'),
  HU_BB:expand('22+,A2s+,A2o+,K2s+,K5o+,Q2s+,Q8o+,J5s+,J8o+,T6s+,T8o+,96s+,98o,85s+,75s+,64s+,54s'),
  HU_3B:expand('88+,A9s+,ATo+,KTs+,KQo'),
  CALLPUSH15:expand('99+,AQs+,AQo+'),
  CALLPUSH10:expand('66+,ATs+,ATo+,KQs'),
};
const PUSH={
  b15:{EP:expand('77+,AJs+,AJo+,KQs,KQo'),MP:expand('55+,ATs+,ATo+,KJs+,KQo'),LATE:expand('22+,A2s+,A7o+,K9s+,KTo+,QTs+,JTs'),BTN:expand('22+,A2s+,A2o+,K8s+,K9o+,Q9s+,QTo+,J9s+,JTo,T9s,98s')},
  b10:{EP:expand('55+,ATs+,ATo+,KJs+,KQo'),MP:expand('33+,A8s+,A9o+,KTs+,KJo+,QJs'),LATE:expand('22+,A2s+,A2o+,K7s+,K9o+,Q9s+,QTo+,J9s+,JTo,T9s'),BTN:expand('22+,A2s+,A2o+,K2s+,K7o+,Q8s+,Q9o+,J8s+,J9o+,T8s+,T9o,97s+,87s,76s')},
  b5:{EP:expand('22+,A2s+,A2o+,K9s+,KTo+,QTs+,JTs'),MP:expand('22+,A2s+,A2o+,K2s+,K7o+,Q8s+,Q9o+,J8s+,J9o+,T8s+,T9o,97s+,87s,76s'),LATE:expand('22+,A2s+,A2o+,K2s+,K2o+,Q2s+,Q5o+,J4s+,J7o+,T6s+,T8o+,96s+,98o,85s+,75s+,64s+,54s'),BTN:expand('22+,A2s+,A2o+,K2s+,K2o+,Q2s+,Q5o+,J4s+,J7o+,T6s+,T8o+,96s+,98o,85s+,75s+,64s+,54s')},
};
const GROUP={UTG:'EP',UTG1:'EP',MP:'MP',LJ:'MP',HJ:'LATE',CO:'LATE',BTN:'BTN',SB:'LATE',BB:null};
const LOOSER={EP:'MP',MP:'LATE',LATE:'BTN',BTN:'BTN'};
const GROUP_RU={EP:'ранней позиции',MP:'средней позиции',LATE:'поздней позиции',BTN:'баттона'};

