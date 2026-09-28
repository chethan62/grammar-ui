function esc(s){var d=document.createElement('div');d.textContent=s;return d.innerHTML}
var currentMatches=[],lastText='',timer=null;

var ta=document.getElementById('text'),resEl=document.getElementById('results'),
    langEl=document.getElementById('lang'),statsEl=document.getElementById('inputStats'),
    issueEl=document.getElementById('issueStats');

function words(t){return t.trim().split(/\s+/).filter(Boolean).length}

async function run(){
  var text=ta.value,lang=langEl.value;
  lastText=text; var wc=words(text);
  statsEl.textContent=text.length+' ch · '+wc+' w';
  if(!wc){resEl.innerHTML='<p class="empty">Start writing…</p>';issueEl.textContent='';return}
  issueEl.textContent='checking…';
  try{
    var r=await fetch('/v2/check',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({text:text,language:lang})});
    var d=await r.json();
    if(ta.value!==text)return;
    currentMatches=d.matches;
    render(text,d.matches);
    issueEl.textContent=d.matches.length+' issue'+(d.matches.length!==1?'s':'');
  }catch(e){issueEl.textContent='Error';resEl.innerHTML='<p class="empty">'+esc(e.message)+'</p>'}
}

function render(text,matches){
  if(!matches.length){resEl.innerHTML='<p class="ok">No issues found</p>';return}
  var parts=[],pos=0,lastEnd=-1;
  var sorted=matches.slice().sort(function(a,b){return a.offset-b.offset});
  for(var i=0;i<sorted.length;i++){
    var m=sorted[i],off=m.offset,len=m.length;
    if(off<lastEnd)continue;
    if(off>pos)parts.push(esc(text.slice(pos,off)));
    parts.push('<mark class="t-'+m.type.typeName+'" title="'+esc(m.message)+'">'+esc(text.slice(off,off+len))+'</mark>');
    pos=off+len;lastEnd=pos;
  }
  if(pos<text.length)parts.push(esc(text.slice(pos)));
  var html='<div class="snippet">'+parts.join('')+'</div>';
  for(var i=0;i<matches.length;i++){
    var m=matches[i];
    html+='<div class="match '+m.type.typeName+'"><div class="msg">'+esc(m.message)+'</div>';
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

function applySuggestion(mi,ri){
  var m=currentMatches[mi];if(!m)return;
  var rep=m.replacements[ri].value,t=ta.value;
  if(m.offset+m.length>t.length)return;
  ta.value=t.slice(0,m.offset)+rep+t.slice(m.offset+m.length);
  run();
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
  var btn=document.querySelector('button.ghost');
  btn.textContent='Copied!';setTimeout(function(){btn.textContent='Copy text'},1500);
}

resEl.addEventListener('click',function(e){
  var b=e.target.closest('.rep');if(!b)return;
  if(b.hasAttribute('data-fix')){
    fixSentence(+b.getAttribute('data-fix'));return;
  }
  applySuggestion(+b.getAttribute('data-mi'),+b.getAttribute('data-ri'));
});
ta.addEventListener('input',function(){
  statsEl.textContent=ta.value.length+' ch · '+words(ta.value)+' w';
  clearTimeout(timer);timer=setTimeout(run,400);
});

fetch('/status').then(function(r){return r.json()}).then(function(d){
  document.getElementById('version').textContent='v'+d.version;
}).catch(function(){});

async function fixSentence(mi){
  var m=currentMatches[mi],t=ta.value;if(!m)return;
  var off=m.offset,s=off;
  while(s>0&&t[s-1]!='.'&&t[s-1]!='!'&&t[s-1]!='?'&&t[s-1]!='\n')s--;
  var e=off;
  while(e<t.length&&t[e]!='.'&&t[e]!='!'&&t[e]!='?'){if(t[e]=='\n'&&e>off)break;e++}
  if(t[e]=='.'||t[e]=='!'||t[e]=='?')e++;
  while(s<e&&(t[s]==' '||t[s]=='.'||t[s]=='!'||t[s]=='?'))s++;
  var btn=document.querySelector('[data-fix="'+mi+'"]');
  if(btn){btn.textContent='Fixing…';btn.disabled=true}
  try{
    var r=await fetch('/v2/fix-sentence',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({text:t,offset:off})});
    var d=await r.json();
    if(d.fixed){ta.value=t.slice(0,s)+d.fixed+t.slice(e);run()}
  }catch(ex){}
  if(btn){btn.textContent=String.fromCharCode(0x270E)+' Fix sentence';btn.disabled=false}
}

run();
