// grammar-ui — talks to any LanguageTool-compatible server.
//
// The API base is a setting, not a constant: this file is served from a
// different origin than the server (or opened from disk), so nothing here may
// assume same-origin.
//
// Escaping discipline: the Issues pane is built as HTML strings, so every value
// that comes from the server or the user goes through esc() — messages,
// replacements, rule ids, error text. The only unescaped strings are the ones
// this file writes itself (class names from cssClass(), which is a fixed switch,
// and the markup literals), and candidate text is read back with textContent,
// never with innerHTML.
// The engine on the same host this page came from. Hardcoding localhost was fine while
// only this machine used the UI; over the LAN the browser is on the phone, where
// localhost is the phone, and the checker looks unreachable. location.hostname is the
// machine that served the page, which is where the engine lives in every deployment.
function apiDefault(host){ return 'http://' + (host || 'localhost') + ':8875' }
var DEFAULT_API = apiDefault(location.hostname);
var API = normalizeApi(localStorage.getItem('grammar-api') || DEFAULT_API);

function api(path){ return API.replace(/\/+$/, '') + path }

// The Fix sentence label. An inline SVG rather than a glyph (U+270E rendered as a box
// wherever the font stack lacks it, and it was the only icon in the UI): a vector icon
// is the same everywhere and inherits the button's colour through currentColor.
var FIX_ICON='<svg viewBox="0 0 24 24" width="12" height="12" fill="currentColor" aria-hidden="true" '+
  'style="vertical-align:-1px"><path d="M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25zM20.71 7.04a1 1 0 0 0 '+
  '0-1.41l-2.34-2.34a1 1 0 0 0-1.41 0l-1.83 1.83 3.75 3.75 1.83-1.83z"/></svg> ';

// Escapes for text AND attribute position. textContent→innerHTML leaves double
// quotes alone, and every server value here lands inside an attribute
// (title="…") where harper's own messages quote the user's words — a quote in a
// message closed the attribute early and injected the rest as markup.
function esc(s){return String(s).replace(/[&<>"']/g,function(c){
  return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]})}
var currentMatches=[],lastText='',timer=null;

var ta=document.getElementById('text'),resEl=document.getElementById('results'),
    langEl=document.getElementById('lang'),statsEl=document.getElementById('inputStats'),
    issueEl=document.getElementById('issueStats'),apiEl=document.getElementById('api'),
    verEl=document.getElementById('version'),
    intentEl=document.getElementById('intent'),toneEl=document.getElementById('tone'),
    levelEl=document.getElementById('level');

function words(t){return t.trim().split(/\s+/).filter(Boolean).length}

// Highlighting follows the LanguageTool category, not the engine's typeName:
// harper calls a spelling mistake "UnknownWord" and everything else "Other",
// which matched no CSS rule at all (the old classes were t-UnknownWord/t-Other,
// so nothing was ever highlighted).
function cssClass(m){
  var c=(m.rule&&m.rule.category&&m.rule.category.id)||'';
  if(c==='TYPOS')return 'spelling';
  if(c==='GRAMMAR')return 'grammar';
  if(c==='TYPOGRAPHY'||c==='CASING')return 'typography';
  return 'style';
}

// The sentence around an offset, used by both "Fix sentence" and "Rephrase".
function sentenceAt(t,off){
  var s=off;
  while(s>0&&'.!?\n'.indexOf(t[s-1])<0)s--;
  var e=off;
  while(e<t.length&&'.!?'.indexOf(t[e])<0){if(t[e]=='\n'&&e>off)break;e++}
  if(e<t.length&&'.!?'.indexOf(t[e])>=0)e++;
  while(s<e&&' .!?'.indexOf(t[s])>=0)s++;
  return {start:s,end:e,text:t.slice(s,e)};
}

function setStatus(text,warn){
  verEl.textContent=text;
  verEl.className='ver'+(warn?' warn':'');
}

// People type "localhost:8875", which the browser reads as the scheme "localhost:"
// and then cannot fetch at all. One missing scheme should not look like a dead
// server, so it is added here rather than reported as unreachable.
function normalizeApi(v){
  v=v.trim();
  return v && !/^[a-z][a-z0-9+.\-]*:\/\//i.test(v) ? 'http://'+v : v;
}

// The API base is user-entered, so "Test" is the only way to tell a typo from a
// stopped server: it asks for /status and reports what came back.
async function saveApi(){
  var v=normalizeApi(apiEl.value);
  if(v){API=v;localStorage.setItem('grammar-api',v)}
  apiEl.value=API;
  var btn=document.getElementById('testBtn');
  btn.textContent='Testing…';
  try{
    var r=await fetch(api('/status'));
    var d=await r.json();
    setStatus('connected · v'+d.version,false);
    run();
  }catch(e){
    setStatus('unreachable',true);
  }
  btn.textContent='Test';
}

async function run(){
  var text=ta.value,lang=langEl.value;
  lastText=text; var wc=words(text);
  statsEl.textContent=text.length+' ch · '+wc+' w';
  if(!wc){resEl.innerHTML='<p class="empty">Start writing…</p>';issueEl.textContent='';return}
  issueEl.textContent='checking…';
  // level=picky asks for the style tier (wordiness, passive voice). Without it the
  // server answers the correctness tier only, so a writing UI would lose the hints
  // that are the reason to run a checker locally at all.
  // Style hints (wordiness, passive voice) are the reason to run a checker
  // locally at all, but they are also the noisiest part: a toggle beats editing
  // the source when the noise gets in the way.
  var body=JSON.stringify({text:text,language:lang,level:levelEl&&levelEl.checked?'picky':'default'});
  var opts={method:'POST',headers:{'Content-Type':'application/json'},body:body};
  try{
    // Stats ride along with the check: they are arithmetic over the string, so
    // the second request costs the server microseconds and saves a round trip.
    var both=await Promise.all([fetch(api('/v2/check'),opts),fetch(api('/v2/stats'),opts)]);
    var d=await both[0].json();
    if(ta.value!==text)return;
    currentMatches=d.matches||[];
    render(text,currentMatches);
    issueEl.textContent=currentMatches.length+' issue'+(currentMatches.length!==1?'s':'');
    if(both[1].ok){
      var st=await both[1].json();
      statsEl.textContent=wc+' w · '+st.grade+' · '+st.readingTime+' · ease '+Math.round(st.fleschReadingEase);
    }
  }catch(e){
    issueEl.textContent='Error';
    resEl.innerHTML='<p class="empty">'+esc(e.message)+' — is the server running at '+esc(API)+'?</p>';
    setStatus('unreachable',true);
  }
}

function render(text,matches){
  if(!matches.length){resEl.innerHTML='<p class="ok">No issues found</p>';return}
  var parts=[],pos=0,lastEnd=-1;
  var sorted=matches.slice().sort(function(a,b){return a.offset-b.offset});
  for(var i=0;i<sorted.length;i++){
    var m=sorted[i],off=m.offset,len=m.length;
    if(off<lastEnd)continue;
    if(off>pos)parts.push(esc(text.slice(pos,off)));
    parts.push('<mark class="t-'+cssClass(m)+'" title="'+esc(m.message)+'">'+esc(text.slice(off,off+len))+'</mark>');
    pos=off+len;lastEnd=pos;
  }
  if(pos<text.length)parts.push(esc(text.slice(pos)));
  var html='<div class="snippet">'+parts.join('')+'</div>';
  for(var i=0;i<matches.length;i++){
    var m=matches[i];
    html+='<div class="match '+cssClass(m)+'"><div class="msg">'+esc(m.message)+'</div>';
    html+='<div class="rule">'+esc(m.rule.id)+' · offset '+m.offset+', len '+m.length+'</div>';
    if(m.replacements.length){
      html+='<div class="reps">';
      for(var j=0;j<m.replacements.length;j++)
        html+='<button class="rep" data-mi="'+i+'" data-ri="'+j+'">'+esc(m.replacements[j].value)+'</button>';
      html+='</div>';
    }
    html+='<button class="rep fix" data-fix="'+i+'">'+FIX_ICON+'Fix sentence</button>';
    html+='</div>';
  }
  resEl.innerHTML=html;
}

function replaceRange(start,end,rep){
  var t=ta.value;
  if(start<0||end>t.length||start>end)return false;
  ta.value=t.slice(0,start)+rep+t.slice(end);
  run();
  return true;
}

function applySuggestion(mi,ri){
  var m=currentMatches[mi];if(!m)return;
  replaceRange(m.offset,m.offset+m.length,m.replacements[ri].value);
}

// Apply every suggestion, right to left so earlier offsets stay valid, skipping the
// ones that overlap a suggestion already applied — the same rule render() uses for
// the highlights, which applyAll was missing. One round can uncover the next: "teh"
// at the start of a sentence is both a misspelling and a capitalisation, and the
// first round settles one of them, which is why one click used to leave issues
// behind and a second click was needed. Repeat until the text stops changing.
async function applyAll(){
  var btn=document.querySelector('.toolbar .primary');
  if(btn)btn.disabled=true;
  for(var pass=0;pass<3;pass++){
    var t=ta.value,ms=currentMatches.slice().sort(function(a,b){return b.offset-a.offset});
    var next=t,floor=t.length,applied=false;
    for(var i=0;i<ms.length;i++){
      var m=ms[i];if(!m.replacements.length)continue;
      if(m.offset+m.length>floor)continue; // overlaps a suggestion already applied
      next=next.slice(0,m.offset)+m.replacements[0].value+next.slice(m.offset+m.length);
      floor=m.offset;applied=true;
    }
    if(!applied||next===t)break;
    ta.value=next;
    await run();
  }
  if(btn)btn.disabled=false;
}

function copyText(){
  ta.select(); document.execCommand('copy');
  var btn=document.getElementById('copyBtn');
  if(!btn)return;
  btn.textContent='Copied!';setTimeout(function(){btn.textContent='Copy text'},1500);
}

// Rephrase the sentence the cursor is in, with the server's local model. The
// endpoint answers 503 with the reason when no model is configured or Ollama is
// down — show that text, it names the fix.
async function rephrase(){
  var t=ta.value,off=(typeof ta.selectionStart==='number'?ta.selectionStart:0);
  var s=sentenceAt(t,off);
  if(!s.text.trim())return;
  var btn=document.getElementById('rephraseBtn');
  var label=btn.textContent;
  btn.disabled=true;btn.textContent='Rephrasing…';
  try{
    var r=await fetch(api('/v2/rewrite'),{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({text:s.text,language:langEl.value,tone:toneEl.value,intent:intentEl.value})});
    if(!r.ok){throw new Error((await r.text()).replace(/^Error: /,''))}
    var d=await r.json();
    renderRephrase(s,d);
  }catch(e){
    resEl.insertAdjacentHTML('afterbegin','<div class="match"><div class="msg">Rephrase unavailable</div>'+
      '<div class="rule">'+esc(e.message)+'</div></div>');
  }
  btn.disabled=false;btn.textContent=label;
}

function renderRephrase(s,d){
  if(!d.candidates||!d.candidates.length)return;
  // "rephrase" on the block, so the buttons in here are never confused with a match's
  // suggestion rows (same .reps/.rep classes, different meaning, different handler).
  var html='<div class="match rephrase"><div class="msg">Rephrase · '+esc(d.provider?d.provider+' / '+d.model:d.model)+' · '+d.elapsedMs+' ms</div>'+
    '<div class="reps">';
  // The source sentence travels with the buttons: a candidate is only valid for the
  // text it was computed from, and the text can change between the request and the
  // click (typing, Fix all, Fix sentence). See the click handler.
  for(var i=0;i<d.candidates.length;i++)
    html+='<button class="rep" data-rep-start="'+s.start+'" data-rep-end="'+s.end+'"'+
      ' data-rep-src="'+esc(s.text)+'">'+esc(d.candidates[i])+'</button>';
  html+='</div><div class="rule">click an alternative to replace the sentence</div></div>';
  resEl.insertAdjacentHTML('afterbegin',html);
}

resEl.addEventListener('click',function(e){
  var b=e.target.closest('.rep');if(!b)return;
  if(b.hasAttribute('data-rep-start')){
    var st=+b.getAttribute('data-rep-start'),en=+b.getAttribute('data-rep-end');
    // Applying a candidate splices the range the sentence had when the model answered.
    // If the text has moved on, that range no longer covers it: the click would cut a
    // span out of the wrong place and silently wreck the document (measured: "Yes. " was
    // dropped and a phrase duplicated). Stale blocks are dropped instead.
    if(ta.value.slice(st,en)!==b.getAttribute('data-rep-src')){
      var stale=b.closest('.rephrase');if(stale)stale.remove();
      return;
    }
    replaceRange(st,en,b.textContent);return;
  }
  if(b.hasAttribute('data-fix')){
    fixSentence(+b.getAttribute('data-fix'));return;
  }
  applySuggestion(+b.getAttribute('data-mi'),+b.getAttribute('data-ri'));
});
ta.addEventListener('input',function(){
  statsEl.textContent=ta.value.length+' ch · '+words(ta.value)+' w';
  // Text changed: any rephrase candidate on screen was computed for the old text, so
  // take the buttons away rather than leave a click that cannot be applied. (The click
  // handler still checks, because Fix all and Fix sentence change the text without
  // firing this event.)
  var staleReps=resEl.querySelectorAll('.rephrase');
  for(var i=0;i<staleReps.length;i++)staleReps[i].remove();
  clearTimeout(timer);timer=setTimeout(run,400);
});
langEl.addEventListener('change',function(){run()});
if(levelEl)levelEl.addEventListener('change',function(){run()});
// Ctrl+Enter is the one shortcut worth having: fixing everything is the action
// you repeat, and reaching for the mouse between sentences is the cost.
ta.addEventListener('keydown',function(e){
  if((e.ctrlKey||e.metaKey)&&e.key==='Enter'){e.preventDefault();applyAll()}
});

async function fixSentence(mi){
  var m=currentMatches[mi],t=ta.value;if(!m)return;
  var s=sentenceAt(t,m.offset);
  var btn=document.querySelector('[data-fix="'+mi+'"]');
  if(btn){btn.textContent='Fixing…';btn.disabled=true}
  try{
    var r=await fetch(api('/v2/fix-sentence'),{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({text:t,offset:m.offset})});
    var d=await r.json();
    if(!d.fixed)return;
    // The server says which range it fixed, and that range comes from the one
    // sentence definition (the one /v2/stats counts with). Replacing our own guess
    // instead is how a fix lands on a different sentence than the server meant —
    // the two disagreed over "Dr. Smith" and "R. K. Rao". Older servers send only
    // `fixed`, so the local sentence stays as the fallback.
    if(typeof d.offset==='number'&&typeof d.length==='number'){
      replaceRange(d.offset,d.offset+d.length,d.fixed);
    }else{
      replaceRange(s.start,s.end,d.fixed);
    }
  }catch(ex){}
  // innerHTML with a constant — our own SVG above, no user input; textContent cannot hold an icon.
  if(btn){btn.innerHTML=FIX_ICON+'Fix sentence';btn.disabled=false}
}

apiEl.value=API;
apiEl.addEventListener('keydown',function(e){if(e.key==='Enter')saveApi()});
saveApi();

// ---- The rewrite backend -------------------------------------------------
// The server owns the model call; this panel is its face. GET /v1/ai reports
// what is configured, whether it answers and what models it offers; POST changes
// it. Only loopback may POST — from the phone that is a 403 — so when the server
// says writable:false the panel explains rather than offering dead buttons.
var aiPanelEl=document.getElementById('aiPanel'),aiBodyEl=document.getElementById('aiBody'),
    aiDotEl=document.getElementById('aiDot'),aiSummaryEl=document.getElementById('aiSummary'),
    ai=null,        // the last GET /v1/ai
    aiTest=null;    // the last backend test, as HTML — see aiTestShow()

function aiDot(state){
  aiDotEl.className='aidot'+(state==='ok'?' on':(state==='bad'?' bad':''));
}
function aiField(id){var e=document.getElementById(id);return e?e.value:''}

async function loadAI(){
  try{
    var r=await fetch(api('/v1/ai'));
    if(!r.ok)throw new Error('HTTP '+r.status);
    ai=await r.json();
    renderAI(ai);
  }catch(e){
    ai=null;aiDot('bad');aiSummaryEl.textContent='unavailable';
    aiBodyEl.innerHTML='<p class="empty">'+esc(e.message)+' — this server has no /v1/ai. Set a model in its config, or update it.</p>';
  }
}

function renderAI(st){
  aiDot(st.reachable?'ok':'bad');
  // The row is a setting, not a status: the label lives in the HTML, so this
  // writes only the current choice.
  var name=st.provider?(st.provider+(st.model?' · '+st.model:'')):'off';
  aiSummaryEl.textContent=name+(st.reachable?'':' · not answering');

  var h='<div class="airow"><label>Runner</label>',i;
  for(i=0;i<st.presets.length;i++){
    var p=st.presets[i];
    h+='<button class="aipick'+(p.id===st.provider?' active':'')+'" data-provider="'+esc(p.id)+'"'+(st.writable?'':' disabled')+'>'+esc(p.label)+'</button>';
  }
  h+='<button class="aipick'+(st.provider==='none'?' active':'')+'" data-provider="none"'+(st.writable?'':' disabled')+'>Off</button>';
  h+='</div>';

  h+='<div class="aifields">'+
     '<label for="aiurl">URL</label><input id="aiurl" spellcheck="false" placeholder="http://127.0.0.1:11434" value="'+esc(st.url||'')+'"'+(st.writable?'':' disabled')+'>'+
     '<label for="aimodel">Model</label><input id="aimodel" spellcheck="false" list="aimodels" value="'+esc(st.model||'')+'"'+(st.writable?'':' disabled')+'>'+
     '<datalist id="aimodels">';
  for(i=0;i<st.models.length;i++)h+='<option value="'+esc(st.models[i])+'">';
  h+='</datalist><button class="primary" id="aiApply" onclick="applyAI()"'+(st.writable?'':' disabled')+'>Apply</button>';
  if(st.provider&&st.provider!=='none'){
    // Available over the LAN too: it changes nothing, it only spends a second of
    // CPU proving the backend can actually answer.
    h+='<button class="ghost" id="aiTest" onclick="testAI()">Test this backend</button>';
  }
  h+='</div>';

  // Rephrase with no backend is a 503 explained in the Issues pane, which is a
  // worse way to learn it than a disabled button that says why up front.
  var ready=!!(st.provider&&st.provider!=='none');
  var rb=document.getElementById('rephraseBtn');
  if(rb){rb.disabled=!ready;rb.title=ready?'':'No AI backend — pick one in the AI backend row above'}

  h+='<div class="ainote aitest" id="aitest">'+(aiTest||'')+'</div>';
  h+='<div class="ainote" id="aimsg">';
  if(!st.writable){
    h+='Read-only from this device: the server accepts settings only from the machine it runs on.';
  }else if(!st.provider){
    h+='Off. Rephrase answers 503 until a backend is chosen.';
  }else if(st.reachable){
    h+=st.models.length?esc(st.models.length+' model'+(st.models.length===1?'':'s')+' available: '+st.models.join(', ')):'reachable, and it named no models';
    h+=st.local?'. Local — your text stays on this machine.':'. Cloud — the sentence you rephrase leaves this machine.';
  }else{
    h+='Not answering at '+esc(st.url)+'. '+esc(st.hint||'');
  }
  if(st.keyEnv)h+=' '+(st.keySet?'Using the key in '+esc(st.keyEnv)+'.':'<b>'+esc(st.keyEnv)+' is not set</b> in the server\u2019s environment.');
  h+='</div>';
  if(st.hint&&st.reachable)h+='<div class="ainote">'+esc(st.hint)+'</div>';
  aiBodyEl.innerHTML=h;
}

function pickAI(id){
  var st=ai;if(!st)return;
  for(var i=0;i<st.presets.length;i++){
    var p=st.presets[i];
    if(p.id!==id)continue;
    // A preset carries its default URL and suggested model, so one click is
    // usually enough: the server fills anything left blank from the same preset.
    var u=document.getElementById('aiurl'),m=document.getElementById('aimodel');
    if(u&&p.url)u.value=p.url;
    if(m)m.value=p.model||'';
  }
  var btns=aiBodyEl.querySelectorAll('.aipick');
  for(var j=0;j<btns.length;j++)btns[j].className='aipick'+(btns[j].getAttribute('data-provider')===id?' active':'');
}

async function applyAI(){
  if(!ai)return;
  var btn=document.getElementById('aiApply');
  var pick=aiBodyEl.querySelector('.aipick.active');
  var provider=pick?pick.getAttribute('data-provider'):ai.provider;
  if(btn){btn.disabled=true;btn.textContent='Applying…'}
  var msg=document.getElementById('aimsg');
  try{
    var r=await fetch(api('/v1/ai'),{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({provider:provider,url:aiField('aiurl'),model:aiField('aimodel')})});
    var d=await r.json();
    if(!r.ok){
      if(msg)msg.innerHTML='<b>'+esc(d.error||('HTTP '+r.status))+'</b>';
    }else{
      ai=d;renderAI(d);aiPanelEl.open=true;
    }
  }catch(e){
    if(msg)msg.innerHTML='<b>'+esc(e.message)+'</b>';
  }
  if(btn){btn.disabled=false;btn.textContent='Apply'}
}

// Does the chosen backend really rephrase? /v1/models answering proves the port
// is open, not that a model is loaded or that it will follow the instruction --
// an LM Studio with no model loaded lists models and cannot rewrite a word. One
// short sentence through the real endpoint is the only honest answer, and the
// milliseconds tell you whether it was warm.
var AI_TEST_TEXT='This sentence is not very clear and it could be made much better.';

// renderAI() replaces the whole panel body, so a result written only into the
// DOM disappears the next time the panel loads (opening it refreshes it). Every
// outcome goes through here: kept in aiTest for the next render, and painted if
// the element is on screen.
function aiTestShow(html){
  aiTest=html;
  var out=document.getElementById('aitest');
  if(out)out.innerHTML=html;
}

async function testAI(){
  var btn=document.getElementById('aiTest');
  if(btn){btn.disabled=true;btn.textContent='Testing…'}
  try{
    var r=await fetch(api('/v2/rewrite'),{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({text:AI_TEST_TEXT,language:langEl.value})});
    var raw=await r.text(),d=null;
    try{d=JSON.parse(raw)}catch(ignore){}
    if(!r.ok||!d||!d.candidates||!d.candidates.length){
      // The server's own message names the fix (start it, pull the model, set the
      // key), so it is shown verbatim rather than replaced with "failed".
      aiTestShow('<b>'+esc((d&&d.error)||raw.slice(0,240)||('HTTP '+r.status))+'</b>');
    }else{
      aiTestShow('Answers: <b>'+esc(d.provider||'?')+' / '+esc(d.model)+'</b> in '+d.elapsedMs+
        ' ms — <b>'+esc(d.candidates[0])+'</b>'+(d.elapsedMs>8000?' (that was a cold model load)':''));
    }
  }catch(e){
    aiTestShow('<b>'+esc(e.message)+'</b>');
  }
  if(btn){btn.disabled=false;btn.textContent='Test this backend'}
}

aiBodyEl.addEventListener('click',function(e){
  var b=e.target.closest('.aipick');if(!b)return;
  pickAI(b.getAttribute('data-provider'));
});
aiPanelEl.addEventListener('toggle',function(){if(aiPanelEl.open)loadAI()});
loadAI();
