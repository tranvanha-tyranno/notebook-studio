import {Dialog, MainAreaWidget, ToolbarButton, showDialog} from '@jupyterlab/apputils';
import {Panel, Widget} from '@lumino/widgets';
import {URLExt} from '@jupyterlab/coreutils';

const size = value => value == null ? '' : value < 1024 ? `${value} B`
  : value < 1024 ** 2 ? `${(value / 1024).toFixed(1)} KiB`
  : value < 1024 ** 3 ? `${(value / 1024 ** 2).toFixed(1)} MiB` : `${(value / 1024 ** 3).toFixed(2)} GiB`;

export function setupFiles(app, tracker, browser, factory, session, settings, setHeaderHidden) {
  const root = (session.workspace || '').replace(/\/$/, '');
  let focus = false, restoreLeft = true, restoreRight = false, desk = null;
  const focusButtons = new Set();
  const fail = error => showDialog({title: 'Thao tác tệp chưa hoàn tất', body: String(error), buttons: [Dialog.okButton()]});
  const run = action => () => Promise.resolve().then(action).catch(fail);
  const fit = () => {app.shell.fit(); app.shell.update();};
  const setFocus = value => {
    if (focus === value) return;
    focus = value;
    document.body.classList.toggle('studio-focus', focus);
    if (focus) {
      restoreLeft = !app.shell.leftCollapsed;
      restoreRight = !app.shell.rightCollapsed;
      app.shell.collapseLeft(); app.shell.collapseRight();
      setHeaderHidden(true);
    } else {
      setHeaderHidden(false);
      if (restoreLeft) app.shell.expandLeft();
      if (restoreRight) app.shell.expandRight();
    }
    for (const control of focusButtons) control.pressed = focus;
    fit();
  };
  const toggleFocus = () => setFocus(!focus);
  app.commands.addCommand('studio:focus', {label: 'Notebook Studio: bật/tắt tập trung', execute: toggleFocus});
  app.commands.addKeyBinding({command: 'studio:focus', keys: ['Accel Shift F10'], selector: 'body'});
  const showFiles = () => {
    setFocus(false);
    app.shell.activateById(browser.id);
    browser.model.refresh().catch(() => {});
  };
  const download = async (view, items) => {
    if (items.length === 1 && items[0].type !== 'directory') return view.model.download(items[0].path);
    const url = new URL(URLExt.join(settings.baseUrl, 'ca2/download'), window.location.href);
    url.searchParams.set('paths', JSON.stringify(items.map(item => item.path)));
    if (settings.token) url.searchParams.set('token', settings.token);
    const anchor = document.createElement('a');
    anchor.href = url.href; anchor.download = ''; anchor.target = '_blank'; anchor.rel = 'noopener';
    document.body.appendChild(anchor); anchor.click(); anchor.remove();
  };
  const addTools = (view, expanded = false) => {
    view.addClass('studio-file-browser');
    view.showFileFilter = true;
    view.showFileSizeColumn = true;
    view.showFullPath = true;
    view.showLastModifiedColumn = expanded;
    view.navigateToCurrentDirectory = false;
    const shortcuts = new Widget(); shortcuts.addClass('studio-file-shortcuts');
    const message = new Widget(); message.addClass('studio-file-message'); message.node.setAttribute('role', 'status');
    const details = new Widget(); details.addClass('studio-file-details');
    const heading = document.createElement('strong'); heading.textContent = 'Chọn tệp hoặc thư mục';
    const info = document.createElement('span'); info.className = 'studio-file-info';
    const path = document.createElement('textarea'); path.readOnly = true; path.rows = 2;
    path.setAttribute('aria-label', 'Đường dẫn đầy đủ trên GPU'); path.spellcheck = false;
    const actions = document.createElement('div'); actions.className = 'studio-file-actions';
    const manage = document.createElement('div'); manage.className = 'studio-file-manage';
    details.node.append(heading, info, path, actions, manage);
    const control = (parent, label, title, action) => {
      const button = document.createElement('button'); button.type = 'button';
      button.textContent = label; button.title = title; button.onclick = run(action); parent.appendChild(button); return button;
    };
    const selected = () => Array.from(view.selectedItems());
    const fullPath = item => root + '/' + (item?.path || view.model.path || '').replace(/^\//, '');
    const copyText = async (text, label) => {
      try {await navigator.clipboard.writeText(text); message.node.textContent = label;}
      catch (_) {
        path.value = text; path.focus(); path.select();
        message.node.textContent = 'Đường dẫn đã được chọn. Nhấn Ctrl+C để sao chép.';
      }
    };
    const copy = control(actions, 'Copy đường dẫn', 'Sao chép đường dẫn tuyệt đối để dùng trong cell',
      () => copyText(selected().length ? selected().map(fullPath).join('\n') : fullPath(), 'Đã copy đường dẫn.'));
    const python = control(actions, 'Copy Python', 'Sao chép đường dẫn có dấu nháy, dán trực tiếp vào Python', () => {
      const paths = selected().length ? selected().map(fullPath) : [fullPath()];
      return copyText(JSON.stringify(paths.length === 1 ? paths[0] : paths), 'Đã copy đường dẫn cho Python.');
    });
    const save = control(actions, 'Tải về', 'Tải tệp; thư mục hoặc nhiều tệp được đóng gói ZIP', () => download(view, selected()));
    save.className = 'studio-file-download';
    const open = control(manage, 'Mở', 'Mở tệp/ảnh hoặc vào thư mục', async () => {
      const item = selected()[0];
      if (item.type === 'directory') await view.model.cd('/' + item.path);
      else view.model.manager.openOrReveal(item.path);
    });
    const rename = control(manage, 'Đổi tên', 'Đổi tên mục đang chọn', () => view.rename());
    control(manage, '+ Thư mục', 'Tạo thư mục trong thư mục đang xem', () => view.createNewDirectory());
    control(shortcuts.node, 'Gốc', 'Mở thư mục gốc của dự án', () => view.model.cd('/'));
    control(shortcuts.node, 'results', 'Xem các file kết quả đã tạo', () => view.model.cd('/results'));
    control(shortcuts.node, 'Làm mới', 'Làm mới danh sách file ngay', () => view.model.refresh());
    if (!expanded) control(shortcuts.node, 'Mở lớn ↗', 'Mở trình quản lý tệp lớn bên cạnh notebook', () => openDesk(true));
    view.header.addWidget(shortcuts);
    view.addWidget(message); view.addWidget(details);
    const refresh = () => {
      const items = selected();
      heading.textContent = items.length > 1 ? `${items.length} mục đã chọn` : items[0]?.name || 'Thư mục đang xem';
      info.textContent = items.length === 1
        ? [items[0].type === 'directory' ? 'Thư mục' : size(items[0].size),
            items[0].last_modified ? new Date(items[0].last_modified).toLocaleString('vi-VN') : ''].filter(Boolean).join(' · ')
        : 'Chọn file để copy đường dẫn hoặc tải về.';
      const value = items.length > 1 ? items.map(fullPath).join('\n') : fullPath(items[0]);
      // Keep text selection intact if the user is copying a path manually.
      if (path.value !== value && document.activeElement !== path) path.value = value;
      save.disabled = !items.length;
      save.textContent = items.length > 1 || items[0]?.type === 'directory' ? 'Tải ZIP' : 'Tải về';
      open.disabled = items.length !== 1; rename.disabled = items.length !== 1;
      copy.disabled = python.disabled = !root;
    };
    // JupyterLab 4.4 exposes selection through DOM events; 4.5 also has a signal.
    view.selectionChanged?.connect(refresh);
    const selection = () => window.requestAnimationFrame(refresh);
    view.node.addEventListener('click', selection);
    view.node.addEventListener('keyup', selection);
    view.model.pathChanged.connect(() => {message.node.textContent = ''; refresh();});
    view.model.refreshed.connect(refresh);
    refresh();
    const interval = window.setInterval(() => {
      if (view.isVisible && !document.hidden) view.model.refresh().catch(() => {});
    }, 3000);
    view.disposed.connect(() => window.clearInterval(interval));
    return view;
  };
  const openDesk = async (beside = true) => {
    setFocus(false);
    if (desk && !desk.isDisposed) {app.shell.activateById(desk.id); return;}
    const view = factory.createFileBrowser('studio-files-expanded', {restore: false, refreshInterval: 3000});
    addTools(view, true);
    const content = new Panel(); content.addClass('studio-file-desk'); content.addWidget(view);
    desk = new MainAreaWidget({content}); desk.id = 'studio-file-manager';
    desk.title.label = 'Quản lý tệp'; desk.title.closable = true;
    desk.toolbar.hide();
    const current = tracker.currentWidget;
    app.shell.collapseLeft();
    app.shell.add(desk, 'main', beside && current ? {mode: 'split-left', ref: current.id} : {mode: 'tab-after'});
    app.shell.activateById(desk.id);
    await view.model.cd('/' + browser.model.path);
  };
  addTools(browser);
  const addNotebookTools = panel => {
    const fileButton = new ToolbarButton({label: 'Tệp', tooltip: 'Mở cây thư mục và file kết quả', onClick: showFiles});
    const focusButton = new ToolbarButton({label: 'Tập trung', tooltip: 'Bật/tắt vùng cell rộng · Ctrl+Shift+F10', pressedTooltip: 'Thoát chế độ tập trung', onClick: toggleFocus});
    panel.toolbar.insertItem(0, 'studio-files', fileButton);
    panel.toolbar.insertItem(1, 'studio-focus', focusButton);
    focusButton.pressed = focus; focusButtons.add(focusButton);
    panel.disposed.connect(() => focusButtons.delete(focusButton));
  };
  tracker.forEach(addNotebookTools);
  tracker.widgetAdded.connect((_, panel) => addNotebookTools(panel));
  app.restored.then(() => {showFiles(); browser.fit(); fit();});
  return {showFiles, toggleFocus, openDesk, leaveFocus: () => setFocus(false)};
}
