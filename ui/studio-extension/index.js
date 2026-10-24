import {INotebookTracker, NotebookActions} from '@jupyterlab/notebook';
import {Dialog, showDialog} from '@jupyterlab/apputils';
import {URLExt} from '@jupyterlab/coreutils';
import {ServerConnection} from '@jupyterlab/services';
import {Widget} from '@lumino/widgets';
import {IDefaultFileBrowser, IFileBrowserFactory} from '@jupyterlab/filebrowser';
import {setupFiles} from './files';

const duration = ms => {
  const seconds = Math.max(0, ms / 1000);
  if (seconds < 1) return `${Math.max(0, Math.round(ms))} ms`;
  if (seconds < 60) return `${seconds.toLocaleString('vi-VN', {maximumFractionDigits: 1})} s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} phút ${Math.floor(seconds % 60)} s`;
  return `${Math.floor(seconds / 3600)} giờ ${Math.floor(seconds % 3600 / 60)} phút`;
};
const clock = ms => {
  const seconds = Math.max(0, Math.floor(ms / 1000));
  return [Math.floor(seconds / 3600), Math.floor(seconds % 3600 / 60), seconds % 60]
    .map(value => String(value).padStart(2, '0')).join(':');
};
const localTime = ms => new Date(ms).toLocaleString('vi-VN');
const timestamp = value => {
  const result = Date.parse(value || '');
  return Number.isFinite(result) ? result : null;
};

export default {
  id: '@ca2/studio:session',
  autoStart: true,
  requires: [INotebookTracker, IDefaultFileBrowser, IFileBrowserFactory],
  activate: async (app, tracker, browser, factory) => {
    const settings = ServerConnection.makeSettings();
    const api = async (method = 'GET', body) => {
      const response = await ServerConnection.makeRequest(
        URLExt.join(settings.baseUrl, 'ca2/session'),
        {method, ...(body ? {body: JSON.stringify(body)} : {})}, settings);
      const result = await response.json();
      if (!response.ok) throw new Error(result.message || `HTTP ${response.status}`);
      return result;
    };
    let session;
    try { session = await api(); } catch (error) {
      console.error('CA2 session information unavailable:', error);
      return;
    }
    // Anchor elapsed time to server uptime, so client clock skew cannot affect it.
    let anchor = performance.now();
    let elapsed = session.elapsed_seconds * 1000;
    const sessionElapsed = () => elapsed + performance.now() - anchor;
    const status = new Widget();
    status.id = 'ca2-session-bar';
    status.node.innerHTML = '<span class="ca2-session-brand">STUDIO</span>'
      + '<button type="button" class="studio-show-files">Tệp</button>'
      + '<button type="button" class="studio-open-files">Quản lý tệp</button>'
      + '<button type="button" class="studio-terminal">Terminal</button>'
      + '<button type="button" class="studio-focus-button">Tập trung</button>'
      + '<span class="ca2-session-gpu"></span><span class="studio-account-label"></span><span class="ca2-session-clock"></span>'
      + '<span class="ca2-session-remaining"></span>'
      + '<button type="button" class="studio-resource-button">Thông số</button>'
      + '<button type="button" class="studio-gpu-button">Đổi GPU…</button>';
    app.shell.add(status, 'header');
    const gpu = status.node.querySelector('.ca2-session-gpu');
    const timer = status.node.querySelector('.ca2-session-clock');
    const remaining = status.node.querySelector('.ca2-session-remaining');
    const button = status.node.querySelector('.studio-gpu-button');
    const resourcePanel = new Widget(); resourcePanel.id = 'studio-resource-panel';
    resourcePanel.title.label = 'Thông số'; resourcePanel.title.caption = 'CPU, RAM, GPU và thời gian phiên';
    resourcePanel.node.innerHTML = '<h2>Tài nguyên phiên</h2><p class="studio-resource-device"></p><div class="ca2-resources"></div><p class="studio-resource-note"></p>';
    resourcePanel.node.querySelector('.studio-resource-device').textContent = session.device;
    resourcePanel.node.querySelector('.studio-resource-note').textContent = 'Cập nhật mỗi 3 giây. Di chuột vào chỉ số để xem cách đo.';
    app.shell.add(resourcePanel, 'right', {rank: 700});
    const resources = resourcePanel.node.querySelector('.ca2-resources');
    const files = setupFiles(app, tracker, browser, factory, session, settings, hidden => status.setHidden(hidden));
    status.node.querySelector('.studio-show-files').onclick = files.showFiles;
    status.node.querySelector('.studio-open-files').onclick = () => files.openDesk(true);
    status.node.querySelector('.studio-focus-button').onclick = files.toggleFocus;
    status.node.querySelector('.studio-terminal').onclick = async () => {
      files.leaveFocus();
      try {await app.commands.execute('terminal:create-new');} catch (error) {
        await showDialog({title: 'Chưa mở được terminal', body: String(error), buttons: [Dialog.okButton()]});
      }
    };
    status.node.querySelector('.studio-resource-button').onclick = () => {
      files.leaveFocus();
      if (resourcePanel.isVisible) app.shell.collapseRight();
      else app.shell.activateById(resourcePanel.id);
    };
    app.restored.then(() => app.shell.collapseRight());
    const meter = (label, percent, detail) => {
      const item = document.createElement('div'); item.className = 'ca2-meter';
      const text = document.createElement('span');
      text.textContent = `${label} · ${percent == null ? '—' : `${percent.toFixed(0)}%`} ${detail || ''}`;
      const bar = document.createElement('progress'); bar.max = 100;
      bar.value = percent ?? 0;
      bar.setAttribute('aria-label', label);
      item.append(text, bar); return item;
    };
    const updateResources = () => {
      const r = session.resources || {};
      const gib = bytes => (bytes / 1024 ** 3).toFixed(1);
      resources.replaceChildren(
        meter(`CPU${r.cpu_scope === 'process' ? ' tiến trình' : ''}`, r.cpu_percent),
        meter(`RAM${r.ram_scope === 'process' ? ' tiến trình' : ''}`, r.ram_percent,
              r.ram_total ? `· ${gib(r.ram_used)}/${gib(r.ram_total)} GiB` : ''),
        meter('GPU', r.gpu_percent),
        meter('VRAM', r.vram_total_mb ? r.vram_used_mb / r.vram_total_mb * 100 : null,
              r.vram_total_mb ? `· ${(r.vram_used_mb / 1024).toFixed(1)}/${(r.vram_total_mb / 1024).toFixed(1)} GiB` : '')
      );
      resources.title = r.ram_scope === 'process'
        ? 'Runtime không cung cấp cgroup: RAM là tổng RSS tiến trình, có thể đếm trùng trang chia sẻ và chưa gồm cache. CPU được chuẩn hóa theo số core đã chọn.'
        : 'Mức dùng của container so với tài nguyên được cấp. Cập nhật mỗi 3 giây.';
    };
    updateResources();
    gpu.textContent = `GPU · ${session.gpu}`;
    gpu.title = session.device;
    const accountLabel = status.node.querySelector('.studio-account-label');
    accountLabel.textContent = session.account_label || '';
    accountLabel.title = session.account_workspace ? `Workspace: ${session.account_workspace}` : '';
    timer.title = `Bắt đầu: ${localTime(session.started_at * 1000)}. Tính từ lúc Jupyter sẵn sàng; không phải thời gian tính phí Modal.`;
    let switching = false;
    button.onclick = async () => {
      const body = new Widget();
      body.node.className = 'ca2-gpu-dialog';
      const label = document.createElement('label');
      label.textContent = 'GPU cho phiên mới';
      const select = document.createElement('select');
      select.setAttribute('aria-label', 'GPU cho phiên mới');
      for (const name of session.gpu_choices) {
        const option = new Option(name === session.gpu ? `${name} (đang dùng)` : name, name);
        option.selected = name === session.gpu;
        select.add(option);
      }
      label.appendChild(select);
      body.node.appendChild(label);
      const note = document.createElement('p');
      note.textContent = 'Notebook sẽ được lưu trước khi đổi. Phiên hiện tại dừng, biến/model trong RAM mất; '
        + 'checkpoint đã lưu được giữ lại. Bạn cần chạy lại các cell cấu hình và nạp model. '
        + 'GPU mới có mức phí riêng trên Modal. Giữ terminal mở để trình duyệt tự mở phiên mới.';
      body.node.appendChild(note);
      const result = await showDialog({title: 'Đổi GPU và mở phiên mới', body,
        buttons: [Dialog.cancelButton({label: 'Giữ phiên hiện tại'}), Dialog.okButton({label: 'Lưu & đổi GPU'})]});
      if (!result.button.accept || select.value === session.gpu) return;
      button.disabled = true;
      try {
        // Await every save before requesting the old server to stop.
        const panels = [];
        tracker.forEach(panel => panels.push(panel));
        await Promise.all(panels.map(panel => panel.context.save()));
        await api('POST', {gpu: select.value});
        switching = true;
        gpu.textContent = `Đang chuyển sang ${select.value}…`;
        remaining.textContent = 'Chờ tab mới mở từ terminal';
        button.textContent = 'Đang mở phiên mới…';
      } catch (error) {
        button.disabled = false;
        await showDialog({title: 'Chưa đổi GPU', body: String(error), buttons: [Dialog.okButton()]});
      }
    };

    const active = new WeakMap();
    NotebookActions.executionScheduled.connect((_, {cell}) => {
      if (cell.model.type === 'code') active.set(cell.model, {queued: Date.now()});
    });
    NotebookActions.executed.connect((_, {cell}) => {active.delete(cell.model);});
    NotebookActions.outputCleared.connect((_, {cell}) => {active.delete(cell.model);});
    const updateCells = () => {
      tracker.forEach(panel => {
        for (const cell of panel.content.widgets) {
          if (cell.model.type !== 'code' || cell.isDisposed) continue;
          const execution = cell.model.getMetadata('execution') || {};
          const start = timestamp(execution['iopub.execute_input']) ?? timestamp(execution['iopub.status.busy']);
          const finish = timestamp(execution['iopub.status.idle']) ?? timestamp(execution['shell.execute_reply']);
          const pending = active.get(cell.model);
          let text = '', state = '';
          if (pending) {
            // Old execution metadata may still exist while the new cell is queued.
            if (start !== null && start >= pending.queued - 500 && (finish === null || finish < start)) {
              text = `Đang chạy · ${duration(Date.now() - start)}`;
              state = 'running';
            } else {
              text = 'Đang chờ kernel…'; state = 'queued';
            }
          } else if (start !== null && finish !== null && finish >= start) {
            const hasError = cell.model.outputs.toJSON().some(item => item.output_type === 'error');
            text = `${hasError ? 'Có lỗi' : 'Lần gần nhất'} · ${duration(finish - start)} · ${localTime(finish)}`;
            state = hasError ? 'error' : 'complete';
          } else if (start !== null && finish === null && panel.sessionContext.session?.kernel?.status === 'busy') {
            text = `Đang chạy · ${duration(Date.now() - start)}`; state = 'running';
          } else if (start !== null && finish === null) {
            text = 'Lần chạy trước chưa hoàn tất'; state = 'interrupted';
          }
          let footer = cell.node.querySelector(':scope > .ca2-cell-time');
          if (!text) { footer?.remove(); continue; }
          if (!footer) {
            footer = document.createElement('div');
            footer.className = 'ca2-cell-time';
            cell.node.appendChild(footer);
          }
          if (footer.textContent !== text) footer.textContent = text;
          footer.dataset.state = state;
          footer.title = 'Thời gian thực thi lần gần nhất của cell; không bao gồm thời gian chờ trong hàng đợi. Ctrl+S để lưu cùng notebook.';
        }
      });
    };
    const tick = () => {
      timer.textContent = `Phiên · ${clock(sessionElapsed())}`;
      if (!switching) {
        const left = Math.max(0, session.duration_seconds * 1000 - sessionElapsed());
        remaining.textContent = left > 0 ? `Còn ${clock(left)}` : 'Phiên đã hết thời gian';
        status.node.dataset.ending = String(left < 5 * 60 * 1000);
      }
      updateCells();
    };
    tick();
    window.setInterval(tick, 500);
    let failures = 0;
    window.setInterval(async () => {
      if (switching) return;
      try {
        session = await api(); elapsed = session.elapsed_seconds * 1000; anchor = performance.now();
        updateResources();
        failures = 0;
      } catch (_) {
        if (++failures >= 2) {
          remaining.textContent = 'Mất kết nối server';
          switching = true;
          button.disabled = true;
        }
      }
    }, 3000);
  }
};
