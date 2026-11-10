const $ = s => document.querySelector(s);
let draft = null;
$('#add-row').onclick = () => {
  if (!$('#invoice').value || !$('#customer').value || !$('#amount').value) { $('#practice-error').textContent = 'Fill all three invoice fields first.'; return; }
  const tr = document.createElement('tr');
  for(const id of ['invoice','customer','amount']) {const td=document.createElement('td');td.textContent=$('#'+id).value;tr.append(td);}
  $('#rows').append(tr); $('#practice-error').textContent='';
};
$('#save-draft').onclick = () => {
  if (!$('#recipient').value || !$('#message').value) {$('#practice-error').textContent='Enter a recipient and message first.';return;}
  draft={recipient:$('#recipient').value,message:$('#message').value};
  $('#draft-status').textContent='Draft saved locally. No email was sent.'; $('#practice-error').textContent='';
};
$('#finish').onclick = () => {
  $('#result').textContent=$('#rows').children.length && draft && $('#posted').checked ? 'Workflow complete: invoice recorded, sheet updated, draft prepared.' : 'Complete the invoice, email draft, and accounting checkbox first.';
};
