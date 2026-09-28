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
var DEFAULT_API = 'http://localhost:8875';
var API = localStorage.getItem('grammar-api') || DEFAULT_API;

function api(path){ return API.replace(/\/+$/, '') + path }

function esc(s){var d=document.createElement('div');d.textContent=s;return d.innerHTML}
var currentMatches=[],lastText='',timer=null;

var ta=document.getElementById('text'),resEl=document.getElementById('results'),
    langEl=document.getElementById('lang'),statsEl=document.getElementById('inputStats'),
    issueEl=document.getElementById('issueStats'),apiEl=document.getElementById('api'),
    verEl=document.getElementById('version');

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

// The API base is user-entered, so "Test" is the only way to tell a typo from a
// stopped server: it asks for /status and reports what came back.
async function saveApi(){
  var v=apiEl.value.trim();
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
  var body=JSON.stringify({text:text,language:lang});
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
    html+='<button class="rep" data-fix="'+i+'" style="margin-top:.35rem;background:var(--accent);border-color:var(--accent);color:#fff">'+String.fromCharCode(0x270E)+' Fix sentence</button>';
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

function applyAll(){
  var t=ta.value,ms=currentMatches.slice().sort(function(a,b){return b.offset-a.offset});
  var changed=false;
  for(var i=0;i<ms.length;i++){
    var m=ms[i];if(!m.replacements.length)continue;
    var rep=m.replacements[0].value;
    if(m.offset+m.length>t.length)continue;
    t=t.slice(0,m.offset)+rep+t.slice(m.offset+m.length);
    changed=true;
  }
  if(changed){ta.value=t;run()}
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
      body:JSON.stringify({text:s.text,language:langEl.value})});
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
  var html='<div class="match"><div class="msg">Rephrase · '+esc(d.model)+' · '+d.elapsedMs+' ms</div>'+
    '<div class="reps">';
  for(var i=0;i<d.candidates.length;i++)
    html+='<button class="rep" data-rep-start="'+s.start+'" data-rep-end="'+s.end+'">'+esc(d.candidates[i])+'</button>';
  html+='</div><div class="rule">click an alternative to replace the sentence</div></div>';
  resEl.insertAdjacentHTML('afterbegin',html);
}

resEl.addEventListener('click',function(e){
  var b=e.target.closest('.rep');if(!b)return;
  if(b.hasAttribute('data-rep-start')){
    replaceRange(+b.getAttribute('data-rep-start'),+b.getAttribute('data-rep-end'),b.textContent);return;
  }
  if(b.hasAttribute('data-fix')){
    fixSentence(+b.getAttribute('data-fix'));return;
  }
  applySuggestion(+b.getAttribute('data-mi'),+b.getAttribute('data-ri'));
});
ta.addEventListener('input',function(){
  statsEl.textContent=ta.value.length+' ch · '+words(ta.value)+' w';
  clearTimeout(timer);timer=setTimeout(run,400);
});
langEl.addEventListener('change',function(){run()});

async function fixSentence(mi){
  var m=currentMatches[mi],t=ta.value;if(!m)return;
  var s=sentenceAt(t,m.offset);
  var btn=document.querySelector('[data-fix="'+mi+'"]');
  if(btn){btn.textContent='Fixing…';btn.disabled=true}
  try{
    var r=await fetch(api('/v2/fix-sentence'),{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({text:t,offset:m.offset})});
    var d=await r.json();
    if(d.fixed)replaceRange(s.start,s.end,d.fixed);
  }catch(ex){}
  if(btn){btn.textContent=String.fromCharCode(0x270E)+' Fix sentence';btn.disabled=false}
}

apiEl.value=API;
apiEl.addEventListener('keydown',function(e){if(e.key==='Enter')saveApi()});
saveApi();
