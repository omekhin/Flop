/* flop-fbk */
/* Ударный режим: дневная цель и серия дней. Хранится только в браузере (localStorage). */
(function(){
  var KEY='flop_days', GKEY='flop_goal';
  function today(d){ d=d||new Date(); return d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0'); }
  function load(){ try{ return JSON.parse(localStorage.getItem(KEY)||'{}')||{}; }catch(e){ return {}; } }
  function save(D){ try{ localStorage.setItem(KEY,JSON.stringify(D)); }catch(e){} }
  function goal(){ var g=20; try{ g=+localStorage.getItem(GKEY)||20; }catch(e){} return g; }
  function total(o){ return o?(o.h||0)+(o.o||0)+(o.p||0):0; }
  function shift(s,n){ var p=s.split('-'); var d=new Date(+p[0],+p[1]-1,+p[2]); d.setDate(d.getDate()+n); return today(d); }
  function info(){
    var D=load(), g=goal(), t=today(), done=function(k){ return total(D[k])>=g; };
    var cur=0, k=done(t)?t:shift(t,-1); while(done(k)){ cur++; k=shift(k,-1); }
    var best=0, run=0, keys=Object.keys(D).sort(); var prev=null;
    keys.forEach(function(k){ if(total(D[k])>=g){ run=(prev&&shift(prev,1)===k)?run+1:1; prev=k; if(run>best) best=run; } });
    var days=keys.filter(function(k){ return total(D[k])>=g; }).length;
    return {streak:cur,best:Math.max(best,cur),today:total(D[t]),goal:g,doneToday:done(t),days:days,data:D};
  }
  function toast(t){ var el=document.createElement('div'); el.className='flop-toast'; el.setAttribute('role','status'); el.textContent=t; document.body.appendChild(el); setTimeout(function(){ el.remove(); },3200); }
  var FLAME='<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M12 2c.6 3.2-1 5-2.6 6.8C8 10.4 6.5 12 6.5 14.8A5.5 5.5 0 0 0 12 20.3a5.5 5.5 0 0 0 5.5-5.5c0-2.3-1-4.1-2.2-5.4-.2 1.4-.9 2.5-2 3 .5-3.3-.1-7.2-1.3-10.4z"/></svg>';
  function badge(){
    var nav=document.querySelector('.sitenav'); if(!nav) return; var i=info();
    var a=nav.querySelector('.streak'); if(!a){ a=document.createElement('a'); a.className='streak'; a.href='stats.html#streak'; nav.appendChild(a); }
    if(!nav.querySelector('.fbk')){ var f=document.createElement('a'); f.className='fbk'; f.href='https://t.me/ilya21314'; f.target='_blank'; f.rel='noopener'; f.textContent='Отзыв'; f.title='Написать автору в Telegram'; nav.insertBefore(f,a); }
    a.classList.toggle('done',i.doneToday);
    a.title=i.doneToday?'Цель дня выполнена. Серия: '+i.streak:'Сегодня '+i.today+' из '+i.goal+'. Серия: '+i.streak;
    a.setAttribute('aria-label',a.title);
    a.innerHTML=FLAME+'<span>'+i.streak+'</span><span class="goal">'+(i.doneToday?'✓':Math.min(i.today,i.goal)+'/'+i.goal)+'</span>';
  }
  function track(kind,n){
    var D=load(), t=today(), o=D[t]||(D[t]={}); var before=total(o)>=goal();
    o[kind]=(o[kind]||0)+(n||1); save(D);
    if(!before&&total(o)>=goal()){ var s=info().streak; toast('Цель дня выполнена! Серия: '+s+' '+plural(s,['день','дня','дней'])+' подряд'); }
    badge();
  }
  function plural(n,f){ n=Math.abs(n)%100; var n1=n%10; if(n>10&&n<20) return f[2]; if(n1>1&&n1<5) return f[1]; if(n1===1) return f[0]; return f[2]; }
  window.Flop={track:track,info:info,goal:goal,setGoal:function(g){ try{ localStorage.setItem(GKEY,String(g)); }catch(e){} badge(); },today:today,shift:shift,total:total,plural:plural,badge:badge};
  if(document.readyState==='loading') document.addEventListener('DOMContentLoaded',badge); else badge();
})();

/* Общий скрипт сайта: Яндекс Метрика и уведомление о cookies.
   Пока METRIKA_ID пустой, ничего не загружается и баннер не показывается.
   Чтобы включить: создай счётчик на metrika.yandex.ru и впиши его номер ниже. */
(function(){
  var METRIKA_ID = '';
  if(!METRIKA_ID) return;
  (function(m,e,t,r,i,k,a){m[i]=m[i]||function(){(m[i].a=m[i].a||[]).push(arguments)};m[i].l=1*new Date();
   k=e.createElement(t),a=e.getElementsByTagName(t)[0],k.async=1,k.src=r,a.parentNode.insertBefore(k,a)})
   (window,document,'script','https://mc.yandex.ru/metrika/tag.js','ym');
  ym(Number(METRIKA_ID),'init',{clickmap:true,trackLinks:true,accurateTrackBounce:true});
  var ok=false; try{ ok=localStorage.getItem('cookie_ok')==='1'; }catch(e){}
  if(ok) return;
  var b=document.createElement('div');
  b.setAttribute('role','region'); b.setAttribute('aria-label','Уведомление о cookies');
  b.style.cssText='position:fixed;left:12px;right:12px;bottom:12px;max-width:560px;margin:0 auto;z-index:99;background:#141a17;color:#eef1ef;border-radius:14px;padding:14px 16px;font:14px/1.45 Onest,system-ui,sans-serif;display:flex;gap:12px;align-items:center;flex-wrap:wrap;box-shadow:0 10px 30px rgba(0,0,0,.25)';
  b.innerHTML='<span style="flex:1 1 260px">Сайт использует cookies и Яндекс Метрику, чтобы считать посещения. <a href="privacy.html" style="color:#9fd8bc">Подробнее</a></span><button type="button" style="min-height:40px;padding:0 18px;border-radius:10px;border:0;background:#47b383;color:#06140d;font:600 14px Onest,system-ui,sans-serif;cursor:pointer">Понятно</button>';
  b.querySelector('button').onclick=function(){ try{ localStorage.setItem('cookie_ok','1'); }catch(e){} b.remove(); };
  document.addEventListener('DOMContentLoaded',function(){ document.body.appendChild(b); });
  if(document.readyState!=='loading') document.body.appendChild(b);
})();

/* Установка как приложение и офлайн-режим */
if('serviceWorker' in navigator && (location.protocol==='https:'||location.hostname==='localhost')){ window.addEventListener('load',function(){ navigator.serviceWorker.register('sw.js').catch(function(){}); }); }
