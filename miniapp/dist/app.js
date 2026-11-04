const $=id=>document.getElementById(id);
let dataset=null,year='',report='turnover';
const titles={turnover:'Сумма сделок',count:'Количество сделок',monthly:'Динамика по месяцам',sellers:'Топ-10 продавцов',status:'Состояния сделок'};
const months=['Янв','Фев','Мар','Апр','Май','Июн','Июл','Авг','Сен','Окт','Ноя','Дек'];
const tg=window.Telegram?.WebApp;
if(tg){tg.ready();tg.expand();if(tg.isVersionAtLeast('6.1')){tg.setHeaderColor('#f5f7fb');tg.setBackgroundColor('#f5f7fb');}}
const fmt=v=>new Intl.NumberFormat('ru-RU',{maximumFractionDigits:2}).format(Number(v));
function precise(value){const [whole,fraction='']=String(value).split('.');return whole.replace(/\B(?=(\d{3})+(?!\d))/g,' ')+(fraction?','+fraction:'');}
function tab(name){document.querySelectorAll('.view').forEach(el=>{el.hidden=el.id!==name;});document.querySelectorAll('nav button').forEach(b=>{b.classList.toggle('active',b.dataset.tab===name);if(b.dataset.tab===name)b.setAttribute('aria-current','page');else b.removeAttribute('aria-current');});window.scrollTo({top:0,behavior:'instant'});if(name==='reports'&&dataset)renderReport();}
function popup(title,text,command=''){ $('dialog-title').textContent=title;$('dialog-text').textContent=text;$('command').textContent=command;$('command').hidden=!command;$('copy-command').hidden=!command;$('bot-link').hidden=!command;$('dialog').showModal();}
function notify(text){$('toast').textContent=text;$('toast').hidden=false;setTimeout(()=>$('toast').hidden=true,2500);}
function reportCommand(){return '/query '+JSON.stringify(dataset.reports[year][report].plan);}
function handoff(data,command){
  // sendData is supported only for a reply-keyboard launch, not a menu/inline launch.
  const fromKeyboard=tg&&tg.platform!=='unknown'&&!tg.initDataUnsafe?.query_id&&!tg.initDataUnsafe?.chat_instance;
  if(fromKeyboard){try{tg.sendData(JSON.stringify(data));return;}catch{}}
  popup('Продолжим в Telegram','Откройте приложение кнопкой «Открыть веб-меню» в клавиатуре бота. Либо скопируйте команду ниже и отправьте её боту.',command);
}
function renderOverview(){
  const r=dataset.reports[year],p=r.turnover.plan.filters;
  $('turnover').textContent=fmt(Number(r.turnover.rows[0]?.value||0)/1e12)+' трлн';
  $('deal-count').textContent=fmt(r.count.matched);
  $('contract-count').textContent=fmt(dataset.snapshot.manifest['contracts.csv'].rows);
  $('period-label').textContent=p.date_from+' — '+p.date_to;
  $('coverage').textContent=dataset.snapshot.start+' — '+dataset.snapshot.end;
  document.querySelectorAll('.selected-year').forEach(el=>el.textContent=year);
  const rows=r.monthly.rows,maximum=Math.max(...rows.map(x=>Number(x.value)),1),chart=$('chart');chart.replaceChildren();
  for(const row of rows){const group=document.createElement('button');group.className='bar-group';const month=months[Number(row.group[0].slice(5))-1];group.setAttribute('aria-label',month+': '+precise(row.value)+' UZS');group.title=month+': '+precise(row.value)+' UZS';const bar=document.createElement('span');bar.className='bar';bar.style.height=(Number(row.value)/maximum*105)+'px';const label=document.createElement('span');label.className='bar-month';label.textContent=month;group.append(bar,label);group.onclick=()=>popup(month+' '+year,precise(row.value)+' UZS\n'+fmt(row.deals)+' сделок\nАннулированные исключены.');chart.append(group);}
}
function renderReport(){
  const r=dataset.reports[year][report];$('report-title').textContent=titles[report];$('report-period').textContent=r.plan.filters.date_from+' — '+r.plan.filters.date_to;
  document.querySelectorAll('.chip').forEach(el=>el.classList.toggle('active',el.dataset.report===report));
  const container=$('report-rows');container.replaceChildren();
  for(const row of r.rows){const line=document.createElement('div');line.className='result-row';const label=document.createElement('div');label.className='result-label';label.textContent=row.name||row.group.join(' / ')||'Итого';if(row.name){const id=document.createElement('small');id.textContent='ИНН '+row.group[0];label.append(id);}const val=document.createElement('strong');val.textContent=precise(row.value)+(r.plan.metric==='turnover'?' UZS':'');line.append(label,val);container.append(line);}
  if(!r.rows.length)container.textContent='За этот период данных нет.';
  $('report-notes').textContent=fmt(r.matched)+' сделок · '+(report==='status'?'Включая аннулированные':'Аннулированные исключены')+'. Показано '+r.rows.length+' из '+r.groups+' групп. Значения из фиксированной выгрузки.';
  $('report-sources').textContent='Источники: '+r.sources.map(x=>x.file).join(', ');
}
document.addEventListener('click',e=>{const button=e.target.closest('button');if(!button)return;if(button.dataset.tab)tab(button.dataset.tab);if(button.dataset.report&&dataset){report=button.dataset.report;tab('reports');}if(button.dataset.question){$('question').value=button.dataset.question;$('question').focus();}});
$('year').onchange=()=>{year=$('year').value;renderOverview();renderReport();};
$('send-report').onclick=()=>{if(dataset)handoff({type:'report',report,period:year},reportCommand());};
$('show-sources').onclick=()=>{if(dataset)popup('Проверяемые источники',Object.entries(dataset.snapshot.manifest).map(([name,x])=>name+' · '+fmt(x.rows)+' записей').join('\n')+'\n\nОбновлено по состоянию на '+dataset.snapshot.end+'. Все данные вымышленные.');};
$('lookup-kind').onchange=()=>{$('lookup-id').placeholder={deal:'Например, 100001',contract:'Например, 100001',participant:'ИНН из 9 цифр',broker:'Например, BK001'}[$('lookup-kind').value];$('lookup-id').value='';};
$('lookup-form').onsubmit=e=>{e.preventDefault();const kind=$('lookup-kind').value,id=$('lookup-id').value.trim().toUpperCase();const valid=kind==='participant'?/^\d{9}$/:kind==='broker'?/^BK\d{3}$/:/^\d{1,20}$/;if(!valid.test(id)){popup('Проверьте номер','Введите корректный идентификатор выбранной записи.');return;}handoff({type:'lookup',kind,id},'/query '+JSON.stringify({action:kind,id}));};
$('question-form').onsubmit=e=>{e.preventDefault();const text=$('question').value.trim();if(!text)return;handoff({type:'question',text},text);};
$('close-dialog').onclick=()=>$('dialog').close();
$('copy-command').onclick=async()=>{try{await navigator.clipboard.writeText($('command').textContent);notify('Команда скопирована');}catch{notify('Выделите и скопируйте команду вручную');}};
$('reload').onclick=()=>location.reload();
async function load(){try{const response=await fetch('./data.json');if(!response.ok)throw Error();dataset=await response.json();year=dataset.years.at(-1);for(const y of [...dataset.years].reverse()){const option=document.createElement('option');option.value=y;const p=dataset.reports[y].turnover.plan.filters;option.textContent=y+(p.date_from!==y+'-01-01'||p.date_to!==y+'-12-31'?' · часть':'');$('year').append(option);}$('year').value=year;$('year').disabled=false;renderOverview();renderReport();$('content').setAttribute('aria-busy','false');}catch{$('load-error').hidden=false;$('turnover').textContent='Нет данных';$('send-report').disabled=true;$('content').setAttribute('aria-busy','false');}}
load();
