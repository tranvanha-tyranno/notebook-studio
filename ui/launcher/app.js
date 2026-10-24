const token = '__STUDIO_TOKEN__';
const $ = id => document.getElementById(id);
let state, selection = null, gpu = 'T4', browseFolder = '', busy = false;
let pendingStop = null;
let saveSession = null, savePath = '', saveSelection = new Set(), saveLoading = false;
let accountSignature = '', pendingLogout = null;
let balanceAccount = null;
const money = value => '$' + value.toFixed(2);
const usd = value => value == null ? '—' : money(Number(value));
const stamp = value => value ? new Date(value * 1000).toLocaleString('vi-VN') : 'Chưa cập nhật';
function showBalance(account) {
  balanceAccount=account.id;
  $('balance-title').textContent='Số dư · '+account.label;
  $('balance-amount').value='';$('balance-message').textContent='';
  $('balance-dialog').showModal();$('balance-amount').focus();
}
function renderBilling() {
  const items=(state.accounts?.items || []).filter(a=>a.connected);
  $('refresh-billing').disabled=items.some(a=>a.billing?.refreshing);
  $('refresh-billing').textContent=items.some(a=>a.billing?.refreshing)?'Đang cập nhật…':'Cập nhật tiền';
  $('billing-overview').replaceChildren(...items.map(account=>{
    const b=account.billing || {}, card=document.createElement('article');card.className='balance-card';
    const title=document.createElement('strong');title.textContent=account.label;
    const workspace=document.createElement('small');workspace.className='hint';workspace.textContent='Workspace: '+(account.workspace || 'chưa xác minh');
    const amount=document.createElement('div');amount.className='balance-amount';amount.textContent=usd(b.balance);
    if(b.balance!=null && Number(b.balance)<5)card.classList.add('balance-low');
    const caption=document.createElement('small');caption.textContent='Credits còn lại · ước tính';
    const costs=document.createElement('p');costs.className='hint';
    costs.textContent=`Chi phí ${b.cycle || 'tháng này'}: ${usd(b.metered)} · Sau điều chỉnh: ${usd(b.billed)}`;
    costs.title='Chi phí gồm toàn bộ workspace; sau điều chỉnh gồm credits, ưu đãi và phí gói, chưa phải hóa đơn cuối kỳ.';
    const used=document.createElement('p');used.className='hint';used.textContent='Credits đã áp dụng: '+usd(b.credits_used);
    const update=document.createElement('small');update.className='hint';update.textContent='Cập nhật: '+stamp(b.updated_at);
    const message=document.createElement('p');message.className='hint balance-message';
    message.textContent=b.error || b.balance_message || (b.balance==null?'Nhập mốc số dư để theo dõi credits còn lại.':'Mốc số dư: '+stamp(b.balance_recorded_at));
    if(b.updated_at && Date.now()/1000-b.updated_at>600)message.textContent+=' Dữ liệu đã hơn 10 phút.';
    const actions=document.createElement('div');actions.className='balance-actions';
    const edit=document.createElement('button');edit.className='text-button';edit.textContent=b.balance==null?'Nhập số dư':'Cập nhật mốc';edit.onclick=()=>showBalance(account);
    const link=document.createElement('a');link.href='https://modal.com/settings/usage';link.target='_blank';link.rel='noreferrer';link.className='text-button';link.textContent='Xem trên Modal ↗';link.title='Trên Modal, chọn workspace '+(account.workspace || account.label)+' để xem đúng số dư.';
    actions.append(edit,link);card.append(title,workspace,amount,caption,costs,used,update,message,actions);return card;
  }));
  if(!items.length)$('billing-overview').textContent='Đăng nhập Modal để xem tiền từng tài khoản.';
}
$('refresh-billing').onclick=async()=>{
  $('refresh-billing').disabled=true;
  try {await api('/api/billing/refresh',{});await update();notice('');}catch(error){notice(error.message);$('refresh-billing').disabled=false;}
};
$('close-balance').onclick=()=>$('balance-dialog').close();
$('balance-form').onsubmit=async event=>{
  event.preventDefault();$('save-balance').disabled=true;$('balance-message').textContent='Đang đối chiếu billing với Modal…';
  try {await api('/api/billing/balance',{id:balanceAccount,amount:$('balance-amount').value});$('balance-dialog').close();await update();}
  catch(error){$('balance-message').textContent=error.message;}
  finally{$('save-balance').disabled=false;}
};
const elapsed = seconds => {
  const s = Math.max(0, Math.floor(seconds));
  return [Math.floor(s / 3600), Math.floor(s % 3600 / 60), s % 60].map(n => String(n).padStart(2, '0')).join(':');
};
const api = async (path, body) => {
  const response = await fetch(path, body === undefined ? {} : {
    method: 'POST', headers: {'Content-Type': 'application/json', 'X-Studio-Token': token}, body: JSON.stringify(body)
  });
  const value = await response.json();
  if (!response.ok) throw new Error(value.error || 'Không kết nối được server local');
  return value;
};
function notice(message) {$('notice').hidden = !message; $('notice').textContent = message;}
function choose(project) {
  selection = project;
  $('folder-name').textContent = project.name || project.folder.split(/[\\/]/).pop();
  $('folder-path').textContent = project.folder;
  $('notebook-name').textContent = project.notebook.split(/[\\/]/).pop();
  $('selected-file').hidden = false;
  updateLaunchButton();
}
function updateLaunchButton() {
  $('launch').disabled = busy || !selection || !state?.python_ready || !$('profile').value;
}
async function accountAction(path, body={}) {
  try {
    $('account-message').textContent='Đang xử lý…';
    await api('/api/accounts/'+path,body);
    await update();
    $('account-message').textContent='';
  } catch(error){$('account-message').textContent=error.message;}
}
function renderAccounts() {
  const accounts=state.accounts || {items:[]};
  const signature=JSON.stringify(accounts);
  if(signature===accountSignature) {updateLaunchButton(); return;}
  accountSignature=signature;
  const previous=$('profile').value;
  $('profile').replaceChildren(...accounts.items.filter(a=>a.connected).map(a=>{
    const option=document.createElement('option'); option.value=a.id;
    option.textContent=a.label+' · còn '+usd(a.billing?.balance)+' (ước tính)'+(a.default?' · mặc định':'');
    return option;
  }));
  if(!accounts.items.some(a=>a.connected)) {
    const option=new Option('Chưa đăng nhập — thêm tài khoản để chạy',''); $('profile').add(option);
  } else if(accounts.items.some(a=>a.connected && a.id===previous)) $('profile').value=previous;
  else if(accounts.default) $('profile').value=accounts.default;
  $('accounts-list').replaceChildren(...accounts.items.map(account=>{
    const card=document.createElement('div');card.className='account-card';
    const name=document.createElement('strong');name.textContent=account.label;
    const detail=document.createElement('p');detail.className='hint';
    const sessions=state.sessions.filter(s=>s.account_id===account.id && !['stopped','error'].includes(s.state)).length;
    detail.textContent=[account.connected?(account.verified_at?'Đã xác minh':'Đã lưu đăng nhập'):'Đã đăng xuất',
      account.workspace?'Workspace: '+account.workspace:'Profile: '+account.profile,
      account.default?'Mặc định cho phiên mới':'',sessions?`${sessions} phiên đang mở`:null,account.source==='CLI'?'Từ Modal CLI':'Từ Studio'].filter(Boolean).join(' · ');
    const actions=document.createElement('div');actions.className='account-card-actions';
    const button=(label,fn)=>{const b=document.createElement('button');b.type='button';b.className='text-button';b.textContent=label;b.onclick=fn;actions.append(b);return b;};
    if(account.connected) {
      if(!account.default) button('Dùng mặc định',()=>accountAction('default',{id:account.id}));
      button('Kiểm tra',()=>accountAction('verify',{id:account.id}));
      button('Đăng xuất',()=>{
        pendingLogout=account.id;
        $('logout-description').textContent=account.source==='CLI'
          ? `Ngắt ${account.label} khỏi Studio. Profile trong Modal CLI được giữ nguyên; có thể kết nối lại. Cần dừng các phiên đang dùng trước.`
          : `Đăng xuất ${account.label} và xóa thông tin đăng nhập do Studio lưu trên máy. Muốn dùng lại cần đăng nhập Modal lần nữa. Cần dừng các phiên đang dùng trước.`;
        $('logout-prompt').hidden=false;
      });
    } else if(account.source==='CLI') button('Kết nối lại',()=>accountAction('reconnect',{id:account.id}));
    const billing=document.createElement('p');billing.className='hint';
    billing.textContent=`Credits còn lại: ${usd(account.billing?.balance)} (ước tính) · Chi phí tháng: ${usd(account.billing?.metered)}`;
    card.append(name,detail,billing,actions);return card;
  }));
  const login=accounts.login; $('login-status').replaceChildren();
  const waiting=login?.state==='waiting'; $('start-login').disabled=waiting;
  if(login) {
    const p=document.createElement('p');p.className='hint';p.textContent=`${login.label}: ${login.message}`;$('login-status').append(p);
    if(waiting && login.url) {
      const link=document.createElement('a');link.className='open-notebook';link.href=login.url;link.target='_blank';link.rel='noreferrer';link.textContent='Mở trang đăng nhập Modal ↗';$('login-status').append(link);
      if(login.code) {const code=document.createElement('p');code.textContent='Mã xác nhận: '+login.code;$('login-status').append(code);}
      const tip=document.createElement('p');tip.className='hint';tip.textContent='Nếu trình duyệt đang vào nhầm tài khoản, chuyển tài khoản trên Modal trước khi xác nhận.';$('login-status').append(tip);
    }
    if(waiting) {
      const cancel=document.createElement('button');cancel.className='text-button';cancel.textContent='Hủy đăng nhập';cancel.onclick=()=>accountAction('cancel');$('login-status').append(cancel);
    }
  }
  updateLaunchButton();
}
const showAccounts=()=>{$('accounts-dialog').showModal();renderAccounts();};
$('manage-accounts').onclick=showAccounts;
$('add-account').onclick=showAccounts;
$('close-accounts').onclick=()=>$('accounts-dialog').close();
$('profile').onchange=updateLaunchButton;
$('refresh-accounts').onclick=async()=>{
  $('refresh-accounts').disabled=true;await accountAction('refresh');$('refresh-accounts').disabled=false;
};
$('login-form').onsubmit=async event=>{event.preventDefault();await accountAction('login',{label:$('account-label').value});};
$('cancel-logout').onclick=()=>{pendingLogout=null;$('logout-prompt').hidden=true;};
$('confirm-logout').onclick=async()=>{
  if(!pendingLogout)return;
  const id=pendingLogout;pendingLogout=null;$('logout-prompt').hidden=true;await accountAction('logout',{id});
};
function estimates() {
  const p = state.prices, cpu = Number($('cpu').value), ram = Number($('ram').value);
  const gpuHour = p.gpu[gpu] * 3600;
  const resourcesHour = (p.cpu * cpu + p.ram * ram) * 3600;
  const minutes = Number($('minutes').value);
  $('total').textContent = money((gpuHour + resourcesHour) * minutes / 60);
  $('gpu-rate').textContent = money(gpuHour);
  $('resource-rate').textContent = money(resourcesHour);
  $('total-rate').textContent = money(gpuHour + resourcesHour);
  $('estimate-duration').textContent = `Cho ${minutes} phút trên ${gpu}`;
  $('price-date').textContent = 'Đối chiếu: ' + new Date(p.checked_at).toLocaleString('vi-VN');
  document.querySelectorAll('.gpu-card').forEach(card => {
    card.setAttribute('aria-checked', String(card.dataset.gpu === gpu));
    card.querySelector('.gpu-price').textContent = `${money(p.gpu[card.dataset.gpu] * 3600)} / giờ`;
  });
}
function renderGpus() {
  const specs = {T4: '16 GB · Tiết kiệm', L4: '24 GB · Cân bằng', A10G: '24 GB · Đa dụng', A100: '40 GB · Hiệu năng cao', H100: '80 GB · Tác vụ lớn'};
  $('gpu-grid').replaceChildren(...Object.keys(state.prices.gpu).map(name => {
    const card = document.createElement('button'); card.className = 'gpu-card'; card.dataset.gpu = name;
    card.setAttribute('role', 'radio'); card.setAttribute('aria-label', name);
    const title = document.createElement('strong'); title.textContent = name;
    const spec = document.createElement('small'); spec.textContent = specs[name];
    const price = document.createElement('span'); price.className = 'gpu-price';
    card.append(title, spec, price); card.onclick = () => {gpu = name; estimates();}; return card;
  }));
}
function renderProjects() {
  $('recent-projects').replaceChildren(...state.projects.slice(0, 6).map(project => {
    const button = document.createElement('button'); button.textContent = project.name; button.title = project.folder;
    button.onclick = () => choose(project); return button;
  }));
}
const names = {starting:'Đang khởi tạo', running:'Đang chạy', stopping:'Đang dừng', stopped:'Đã dừng', error:'Có lỗi', unknown:'Cần kiểm tra'};
function renderSessions() {
  const container = $('sessions');
  const opened = new Set(Array.from(container.querySelectorAll('details[open]')).map(d => d.dataset.id));
  $('session-count').textContent = state.sessions.filter(s => !['stopped','error'].includes(s.state)).length;
  if (!state.sessions.length) return;
  container.replaceChildren(...state.sessions.map(s => {
    const card = document.createElement('article'); card.className = 'session';
    const top = document.createElement('div'); top.className = 'session-top';
    const info = document.createElement('div');
    const title = document.createElement('div'); title.className = 'session-title'; title.textContent = s.name;
    const notebook = document.createElement('small'); notebook.textContent = s.notebook.split(/[\\/]/).pop(); title.append(notebook);
    const meta = document.createElement('div'); meta.className = 'session-meta';
    [s.account_label || s.profile || 'Tài khoản cũ', s.gpu, `${s.cpu} CPU · ${s.ram} GiB RAM`, `Đã mở ${elapsed(s.elapsed)}`, `Compute ước tính ${money(s.estimated_cost)}`].forEach(text => {
      const span = document.createElement('span'); span.textContent = text; meta.append(span);
    });
    info.append(title, meta);
    const actions = document.createElement('div'); actions.className = 'session-actions';
    const badge = document.createElement('span'); badge.className = 'badge ' + s.state; badge.textContent = names[s.state]; actions.append(badge);
    if (s.url) {
      const link = document.createElement('a'); link.className = 'open-notebook'; link.href = s.url; link.target = '_blank'; link.rel = 'noreferrer'; link.textContent = 'Mở notebook ↗'; actions.append(link);
    }
    const saveLocal = document.createElement('button'); saveLocal.className = 'secondary';
    saveLocal.textContent = 'Lưu file về máy';
    saveLocal.onclick = () => openSave(s.id);
    actions.append(saveLocal);
    if (!['stopped','error'].includes(s.state)) {
      const stop = document.createElement('button'); stop.className = 'stop-session'; stop.textContent = 'Dừng phiên'; stop.disabled = s.state === 'stopping';
      stop.onclick = async () => {
        pendingStop = s.id;
        $('stop-dialog').showModal();
      }; actions.append(stop);
    }
    top.append(info, actions); card.append(top);
    const details = document.createElement('details'); details.dataset.id = s.id; details.open = opened.has(s.id);
    const summary = document.createElement('summary'); summary.textContent = 'Xem log khởi động & thông tin phiên';
    const path = document.createElement('p'); path.className = 'hint'; path.textContent = s.folder;
    const pre = document.createElement('pre'); pre.textContent = s.logs.join('\n');
    details.append(summary, path);
    if (s.dashboard) {
      const link = document.createElement('a'); link.href = s.dashboard; link.target = '_blank'; link.rel = 'noreferrer'; link.className = 'text-button'; link.textContent = 'Mở app trên Modal ↗'; details.append(link);
    }
    details.append(pre); card.append(details); return card;
  }));
}
async function update() {
  state = await api('/api/status'); renderAccounts(); renderBilling(); renderProjects(); renderSessions(); estimates();
  if (!state.python_ready) notice('Chạy .\\modal.ps1 setup một lần để cài Modal client.');
  renderTransfers();
}
function renderTransfers() {
  if (!saveSession) return;
  const transfers = (state.transfers || []).filter(item => item.session_id === saveSession);
  $('save-progress').replaceChildren(...transfers.map(transfer => {
    const card = document.createElement('div'); card.className = 'save-transfer';
    const message = document.createElement('strong'); message.textContent = `${transfer.state === 'complete' ? '✓ ' : ''}${transfer.message}`;
    const path = document.createElement('p'); path.className = 'hint'; path.textContent = transfer.destination;
    card.append(message, path);
    if (transfer.state === 'complete') {
      const open = document.createElement('button'); open.className='secondary'; open.textContent='Mở thư mục đã lưu';
      open.onclick=async () => {try {await api('/api/open-saved',{id:transfer.id});} catch(error){$('save-message').textContent=error.message;}};
      card.append(open);
    }
    return card;
  }));
  const saving=transfers.some(t=>t.state==='saving');
  $('save-folder').disabled=saving || saveLoading;
  $('save-selected').disabled=saving || saveLoading || !saveSelection.size;
}
async function loadSaveFiles(path) {
  saveLoading=true;
  $('save-message').textContent='Đang đọc file trên Volume…';
  $('save-folder').disabled=$('save-selected').disabled=true;
  try {
    const result=await api('/api/files',{id:saveSession,path});
    savePath=result.path; saveSelection=new Set(); $('save-path').textContent='/'+savePath;
    $('save-items').replaceChildren(...result.items.map(item=>{
      const row=document.createElement('div'); row.className='save-file-row';
      const check=document.createElement('input'); check.type='checkbox'; check.setAttribute('aria-label',`Chọn ${item.name}`);
      check.onchange=()=>{if(check.checked)saveSelection.add(item.path);else saveSelection.delete(item.path);renderTransfers();};
      const name=document.createElement('button'); name.className='text-button'; name.textContent=(item.folder?'📁 ':'')+item.name;
      name.onclick=()=>{if(item.folder)loadSaveFiles(item.path);else{check.checked=!check.checked;check.onchange();}};
      const bytes=document.createElement('small'); bytes.textContent=item.folder?'Thư mục':(item.size/1024).toFixed(1)+' KiB';
      row.append(check,name,bytes);return row;
    }));
    $('save-parent').disabled=!savePath;
    $('save-message').textContent=result.items.length?'Chọn file/thư mục rồi bấm Lưu. File cũ trên máy không bị ghi đè.':'Thư mục này chưa có file đã đồng bộ.';
  } catch(error){$('save-message').textContent=error.message;}
  finally {saveLoading=false;}
  renderTransfers();
}
async function openSave(id) {
  saveSession=id; savePath=''; saveSelection=new Set(); $('save-items').replaceChildren(); $('save-progress').replaceChildren(); $('save-dialog').showModal();
  await loadSaveFiles('');
}
async function saveLocal(paths) {
  try {await api('/api/save-local',{id:saveSession,paths});await update();}
  catch(error){$('save-message').textContent=error.message;}
}
$('close-save').onclick=()=>$('save-dialog').close();
$('save-parent').onclick=()=>loadSaveFiles(savePath.split('/').slice(0,-1).join('/'));
$('save-folder').onclick=()=>saveLocal([savePath]);
$('save-selected').onclick=()=>saveLocal([...saveSelection]);
async function browse(path) {
  try {
    const folder = await api('/api/folder?path=' + encodeURIComponent(path));
    browseFolder = folder.path; $('browse-path').value = folder.path;
    $('parent-folder').onclick = () => browse(folder.parent);
    $('folder-items').replaceChildren(...folder.items.map(item => {
      const button = document.createElement('button'); button.className = 'folder-item';
      const kind = document.createElement('span'); kind.textContent = item.folder ? 'DIR' : 'IPYNB';
      const name = document.createElement('span'); name.textContent = item.name;
      const arrow = document.createElement('span'); arrow.textContent = item.folder ? '›' : 'Chọn';
      button.append(kind, name, arrow);
      button.onclick = () => {
        if (item.folder) return browse(item.path);
        choose({folder:folder.path, notebook:item.path}); $('folder-dialog').close(); notice('');
      }; return button;
    }));
    if (!folder.items.length) {const p = document.createElement('p'); p.className='hint'; p.textContent='Không có thư mục hoặc file .ipynb ở đây.'; $('folder-items').append(p);}
  } catch (error) {notice(error.message);}
}
$('browse').onclick = async () => {await browse(selection?.folder || state.project_root); $('folder-dialog').showModal();};
$('close-folder').onclick = () => $('folder-dialog').close();
$('cancel-stop').onclick = () => {pendingStop = null; $('stop-dialog').close();};
$('confirm-stop').onclick = async () => {
  const id = pendingStop; pendingStop = null; $('stop-dialog').close();
  if (!id) return;
  try {await api('/api/stop', {id}); await update();} catch(error) {notice(error.message);}
};
$('path-form').onsubmit = event => {event.preventDefault(); browse($('browse-path').value);};
['minutes','cpu','ram'].forEach(id => $(id).onchange = estimates);
$('refresh-prices').onclick = async () => {
  $('refresh-prices').disabled = true;
  try {state.prices = await api('/api/prices', {}); estimates(); notice('');} catch(error) {notice(error.message);}
  finally {$('refresh-prices').disabled = false;}
};
$('launch').onclick = async () => {
  if (!selection || busy) return;
  busy = true; $('launch').disabled = true; notice('');
  try {
    await api('/api/start', {...selection, gpu, minutes:Number($('minutes').value), cpu:Number($('cpu').value), ram:Number($('ram').value),
      epochs:Number($('epochs').value), split:$('split').value, packages:$('packages').value, data_volume:$('data-volume').value,
      account_id:$('profile').value, include_code:$('include-code').checked, refresh_local:$('refresh-local').checked,
      predict_only:$('predict-only').checked, fresh_train:$('fresh-train').checked});
    await update(); $('sessions').scrollIntoView({behavior:'smooth',block:'center'});
  } catch(error) {notice(error.message);} finally {busy=false; updateLaunchButton();}
};
try {
  state = await api('/api/status'); renderAccounts(); renderBilling(); renderGpus(); renderProjects(); renderSessions(); estimates();
  if (state.projects.length) choose(state.projects[0]);
  if (!state.python_ready) notice('Chạy .\\modal.ps1 setup một lần để cài Modal client.');
  setInterval(() => update().catch(error => notice('Server local đã dừng hoặc mất kết nối.')), 2500);
} catch(error) {notice(error.message);}
