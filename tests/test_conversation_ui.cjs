// Testa o controlador da tela sem navegador, Firebase ou chamadas ao Google.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const { webcrypto } = require('node:crypto');

function element() {
  return { value: '', innerHTML: '', textContent: '', placeholder: 'Descreva o problema',
    disabled: false, listeners: {}, addEventListener(name, fn) { this.listeners[name] = fn; },
    dispatchEvent() {}, focus() {}, close() {}, showModal() { this.open = true; } };
}
const elements = new Map();
const $ = selector => {
  if (!elements.has(selector)) elements.set(selector, element());
  return elements.get(selector);
};
const calls = [];
const timers = new Map();
const waitingLabel = element();
let timerId = 0;
let handler;
$('#knowledge-conversation').querySelector = selector =>
  selector === '[data-wait-label]' && $('#knowledge-conversation').innerHTML.includes('data-wait-label') ? waitingLabel : null;
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const context = vm.createContext({ $, esc, crypto: webcrypto, Event: class {},
  setTimeout: (callback, delay) => { timers.set(++timerId, {callback, delay}); return timerId; },
  clearTimeout: id => timers.delete(id),
  session: { token: 'fixture-only' }, document: { body: { dataset: { page: 'knowledge' } } },
  location: { replace() { throw Error('unexpected redirect'); } }, logout() {},
  api: async (path, options = {}) => { calls.push({ path, body: structuredClone(options.body) }); return handler(path, options.body); }
});
vm.runInContext(fs.readFileSync('acervo.js', 'utf8'), context);
const tick = () => new Promise(resolve => setImmediate(resolve));
async function send(text) {
  $('#knowledge-query').value = text;
  $('#knowledge-form').listeners.submit({ preventDefault() {} });
  await tick();
}
const ticket = {document_key:'a',source_id:'caso',title:'Ticket de teste <PLC>',excerpt:'Trecho técnico',
  metadata:{},origin:{registro_id:'caso',arquivo_relativo:'casos.jsonl'},position:1,start:0,end:14,has_solution:true,ticket_reference:'11813'};
const secondTicket = {...ticket,document_key:'b',source_id:'segundo',ticket_reference:'12000',title:'Segundo ticket citado'};
const discardedTicket = {...ticket,document_key:'c',source_id:'descartado',ticket_reference:'99999',
  title:'Ticket descartado da resposta',has_solution:false,evidence_status:'sem_solucao_identificada'};
const duplicateTicket = {...ticket,document_key:'a2',source_id:'outro-caso-mesmo-ticket'};
const base = { results: [ticket,secondTicket,discardedTicket,duplicateTicket], warnings: [], generation_available: true, generated_answer: null };
(async () => {
  const firstAnswer = { ...base, session_id: 'fixture-session', turn: 1, decision: 'ask',
    follow_up: 'Parou após uma alteração?', messages: [{message:'Falha <PLC>',asked:''}],
    clarification_options: [{label:'Após atualizar',quote:'Fonte fictícia',ticket_reference:'20896'},{label:'Nunca funcionou <PLC>',quote:'Outra fonte fictícia'}],
    usage: {input_tokens:500} };
  let finishFirstRequest;
  handler = () => new Promise(resolve => { finishFirstRequest = () => resolve(firstAnswer); });
  await send('Falha <PLC>');
  assert.match($('#knowledge-conversation').innerHTML, /role="status".*Consultando o acervo.*knowledge-thinking-dots/);
  assert.equal($('#knowledge-submit').disabled, true);
  const [waitTimer] = timers.values();
  assert.equal(waitTimer.delay, 8000);
  waitTimer.callback();
  assert.equal(waitingLabel.textContent, 'A consulta ainda está em andamento.');
  finishFirstRequest();
  await tick();
  assert.doesNotMatch($('#knowledge-conversation').innerHTML, /knowledge-thinking/);
  assert.equal(timers.size, 0);
  assert.equal(calls[0].path, '/knowledge/conversation');
  assert.match($('#knowledge-conversation').innerHTML, /Falha &lt;PLC&gt;/);
  assert.match($('#knowledge-conversation').innerHTML, /Parou após/);
  assert.equal($('#knowledge-query').value, '');
  assert.equal($('#knowledge-submit').disabled, false);
  assert.match($('#knowledge-conversation').innerHTML, /data-option="0"/);
  assert.match($('#knowledge-conversation').innerHTML, /<ol class="knowledge-options"/);
  assert.doesNotMatch($('#knowledge-conversation').innerHTML, /<strong>Assistente/);
  assert.match($('#knowledge-conversation').innerHTML, /Nunca funcionou &lt;PLC&gt;/);
  assert.doesNotMatch($('#knowledge-conversation').innerHTML, /Ticket de teste/, 'candidatos ficam fora da conversa');
  assert.doesNotMatch($('#knowledge-results').innerHTML, /<details[^>]*\bopen\b/);
  assert.match($('#knowledge-results').innerHTML, /<summary>Informações da busca<\/summary>/);
  assert.match($('#knowledge-results').innerHTML, /<details class="knowledge-related"><summary>Fontes e casos relacionados/);
  const beforeOther = calls.length;
  $('#knowledge-conversation').listeners.click({target:{closest(selector) { return selector === '[data-other]' ? {} : null; }}});
  assert.equal(calls.length, beforeOther, 'escrever outra resposta não envia nada automaticamente');
  assert.match($('#knowledge-query').placeholder, /Descreva/);

  handler = () => ({ ...base, session_id: 'fixture-session', turn: 2, decision: 'answer',
    messages: [{message:'Falha <PLC>',asked:''},{message:'Após atualizar',asked:'Parou após uma alteração?'}],
    generated_answer: {paragraphs:[{text:'Confira a versão <driver>',citations:[
      {label:'F1',quote:'Versão incompatível',document_key:'a',source_id:'caso'},
      {label:'F2',quote:'Ação no segundo caso',document_key:'b',source_id:'segundo'}]},
      {text:'Confira as condições antes de aplicar.',citations:[{label:'F3',quote:'Condições do mesmo ticket',document_key:'a2',source_id:'outro-caso-mesmo-ticket'}]}]} });
  $('#knowledge-conversation').listeners.click({target:{closest(selector) { return selector === '[data-option]' ? {dataset:{option:'0'}} : null; }}});
  await tick();
  assert.equal(calls[1].body.message, 'Após atualizar');
  assert.equal(calls[1].body.session_id, 'fixture-session');
  assert.notEqual(calls[0].body.request_id, calls[1].body.request_id);
  assert.match($('#knowledge-conversation').innerHTML, /Confira a versão &lt;driver&gt;/);
  assert.doesNotMatch($('#knowledge-results').innerHTML, /Confira a versão &lt;driver&gt;/, 'resposta não se repete fora da conversa');
  assert.doesNotMatch($('#knowledge-conversation').innerHTML, /Confira a orientação e as fontes abaixo/);
  const answerMarkup = $('#knowledge-conversation').innerHTML;
  assert.match(answerMarkup, /class="knowledge-answer".*Confira a versão/s);
  assert.match(answerMarkup, /<summary>Fontes consultadas<\/summary>/);
  assert.doesNotMatch(answerMarkup, /<details[^>]*\bopen\b/);
  assert.match(answerMarkup, /Ticket #11813/);
  assert.match(answerMarkup, /Ticket #12000/);
  assert.equal((answerMarkup.match(/Ticket #11813/g) || []).length, 1, 'mesmo ticket citado mais de uma vez aparece uma vez');
  assert.doesNotMatch(answerMarkup, /99999|Ticket descartado/, 'candidato não citado não aparece');
  assert.match($('#knowledge-results').innerHTML, /<details class="knowledge-related"><summary>Fontes e casos relacionados/);
  const count = calls.length;
  await send('O problema continua');
  assert.equal(calls.length, count + 1);
  assert.equal(calls.at(-1).body.session_id, 'fixture-session', 'a orientação permite continuar a conversa');
  handler = () => ({title:'Fonte completa',metadata:{},origin:{},source_id:'caso',ticket_details:{
    evidence_status:'sem_solucao_identificada',problem:'Conteúdo <seguro>',context:[],
    available_interactions:1,declared_interactions:2,derived_history:true,
    interactions:[{reference:'interacao:123',date:null,text:'Relato <histórico>'}]}});
  const documentButton = {dataset:{document:'a'}};
  $('#knowledge-conversation').listeners.click({target:{closest(selector) { return selector === '[data-document]' ? documentButton : null; }}});
  await tick();
  assert.equal(calls.at(-1).path, '/knowledge/documents/a');
  assert.equal($('#knowledge-document').open, true);
  assert.match($('#knowledge-document-body').innerHTML, /Conteúdo &lt;seguro&gt;/);
  assert.match($('#knowledge-document-body').innerHTML, /1 no acervo · 2 declaradas · fonte derivada/);
  assert.match($('#knowledge-document-body').innerHTML, /Relato &lt;histórico&gt;/);

  $('#knowledge-reset').onclick();
  assert.equal($('#knowledge-conversation').innerHTML, '');
  handler = () => { throw Error('Falha de rede simulada'); };
  await send('Novo problema');
  assert.match($('#knowledge-conversation').innerHTML, /Falha de rede simulada.*Tentar novamente/s);
  assert.equal(timers.size, 0);
  const failedId = calls.filter(call => call.path === '/knowledge/conversation').at(-1).body.request_id;
  assert.equal($('#knowledge-query').value, 'Novo problema');
  assert.equal($('#knowledge-submit').disabled, false);
  handler = () => ({ ...base, session_id:'next-session',turn:1,decision:'ask',follow_up:'Qual o erro?',messages:[] });
  await send('Novo problema');
  assert.equal(calls.at(-1).body.request_id, failedId, 'tentativa de rede preserva idempotência');
  assert.equal(calls.at(-1).body.session_id, undefined);

  $('#knowledge-reset').onclick();
  handler = () => ({ ...base, session_id:'local-fallback',turn:1,decision:'insufficient',generation_status:'disabled',generation_available:false,
    messages:[{message:'Consulta local',asked:''}] });
  await send('Consulta local');
  assert.equal(calls.at(-1).path, '/knowledge/conversation');
  assert.match($('#knowledge-conversation').innerHTML, /Encontrei atendimentos relacionados/);
  assert.match($('#knowledge-results').innerHTML, /Busca local.*desativado/s);
  assert.match($('#knowledge-results').innerHTML, /Atendimentos relacionados/);
  assert.match($('#knowledge-results').innerHTML, /Evidências técnicas relacionadas/);
  assert.match($('#knowledge-results').innerHTML, /knowledge-ticket-grid/);
  assert.doesNotMatch($('#knowledge-conversation').innerHTML, /class="knowledge-finding"/);
  assert.match($('#knowledge-results').innerHTML, /Ticket descartado/, 'o fallback exibe os candidatos locais');
  handler = () => ({ ...base, session_id:'fresh',turn:1,decision:'insufficient',messages:[] });
  await send('Nova pergunta');
  assert.equal(calls.at(-1).body.session_id, 'local-fallback', 'o envio híbrido mantém o contexto da conversa');
  $('#knowledge-reset').onclick();
  handler = () => ({...base,session_id:'retry-session',turn:1,decision:'insufficient',generation_status:'rejected',
    warnings:['A resposta não passou pela verificação de formato e referências.'],messages:[{message:'Erro ao clicar na Unit',asked:''}]});
  await send('Erro ao clicar na Unit');
  assert.match($('#knowledge-results').innerHTML, /Não foi possível validar a orientação/);
  assert.doesNotMatch($('#knowledge-conversation').innerHTML, /descrever melhor|informações suficientes/);
  assert.match($('#knowledge-conversation').innerHTML, /data-retry/);
  const rejectedRequest = calls.at(-1).body.request_id;
  handler = () => ({...base,session_id:'retry-session',turn:1,decision:'answer',generation_status:'generated',
    messages:[{message:'Erro ao clicar na Unit',asked:''}],generated_answer:{paragraphs:[{text:'Ação histórica registrada.',citations:[{label:'F1',quote:'Trecho original',document_key:'a',source_id:'caso'}]}]}});
  $('#knowledge-conversation').listeners.click({target:{closest(selector){return selector==='[data-retry]' ? {} : null;}}});
  await tick();
  assert.equal(calls.at(-1).body.message,'Erro ao clicar na Unit');
  assert.equal(calls.at(-1).body.session_id,'retry-session');
  assert.notEqual(calls.at(-1).body.request_id,rejectedRequest);
  assert.match($('#knowledge-conversation').innerHTML,/Ação histórica registrada/);

  $('#knowledge-reset').onclick();
  const interaction = {label:'F1',document_key:'i',source_id:'interacao:215807',ticket_reference:'11813'};
  handler = () => ({...base,session_id:'diagnosis-session',turn:1,decision:'answer',
    follow_up:'Qual é a mensagem da exceção?',triage:{status:'selected',candidates:17,selected:3},
    messages:[{message:'Exceção na tela de Rastreabilidade',asked:''}],
    generated_answer:{historical:true,related:true,paragraphs:[{
      text:'Versão textual da resposta',finding:{ticket:'11813',fields:{problem:'Falha ao abrir a árvore',
        diagnosis:'A consulta ao banco demorou'},missing:'A etapa da falha atual'},
      citations:[{...interaction,quote:'Falha ao abrir a árvore'},{...interaction,quote:'A consulta ao banco demorou'}],
    }]}});
  await send('Exceção na tela de Rastreabilidade');
  const finding = $('#knowledge-conversation').innerHTML;
  assert.match(finding, /Caso semelhante.*Sem correção registrada/s);
  assert.match(finding, /Interação #215807.*<summary>Ver detalhes<\/summary>.*Qual é a mensagem/s);
  assert.equal((finding.match(/Interação #215807/g) || []).length, 1);
  assert.doesNotMatch(finding, /Ação registrada|Resultado observado|Versão textual/);
  assert.match($('#knowledge-results').innerHTML, /<summary>Informações da busca<\/summary>.*17 resultados comparados/s);
  console.log('OK: resposta com múltiplos tickets citados, deduplicação, descarte de candidatos, detalhes e conversa.');
})().catch(error => { console.error(error); process.exitCode = 1; });
