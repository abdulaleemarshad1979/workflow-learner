const status = document.querySelector('#status');
async function update() {
  const state = await chrome.runtime.sendMessage({type: 'refresh'});
  status.textContent = state.connected === false ? 'Local app not reachable. Start Workflow Learner; pairing is automatic.' : state.recording ? 'Recording is ON' : state.running ? 'Replaying a workflow' : 'Connected · Recording is off';
}
document.querySelector('#pair').addEventListener('click', async () => {
  await chrome.runtime.sendMessage({type: 'pair', token: document.querySelector('#token').value.trim()});
  document.querySelector('#token').value = '';
  update();
});
update();

setInterval(update, 1500);
