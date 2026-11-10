const status = document.querySelector('#status');
async function update() {
  const state = await chrome.runtime.sendMessage({type: 'refresh'});
  status.textContent = state.connected === false ? 'Start the local app and check your pairing key.' : state.recording ? 'Recording is ON' : state.running ? 'Replaying a workflow' : 'Connected · Recording is off';
}
document.querySelector('#pair').addEventListener('click', async () => {
  await chrome.runtime.sendMessage({type: 'pair', token: document.querySelector('#token').value.trim()});
  document.querySelector('#token').value = '';
  update();
});
update();

setInterval(update, 1500);
