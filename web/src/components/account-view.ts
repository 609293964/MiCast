import type { State } from "../state";

function mijiaIcon(): string {
  return '<img class="mijia-brand-icon" src="assets/brands/mijia-app.png" alt="">';
}

export function renderAccountView(state: State): string {
  return `<button class="button plain back-link" data-account-back>‹ 返回设置</button>${renderAccountBody(state)}`;
}

function renderAccountBody(state: State): string {
  const { xiaomi } = state;
  const connectionError = state.deviceLoadError;
  if (xiaomi.logged_in) {
    if (connectionError) {
      const credentialsInvalid = /HTTP 401|登录已失效|重新连接/.test(connectionError);
      return `
        <div class="page-heading">
          <h2 class="page-title">服务</h2>
          <p>连接音箱品牌服务，自动同步可用设备。</p>
        </div>
        <div class="group-header">需要处理</div>
        <div class="group">
          <div class="cell provider-row">
            <div class="cell-icon device-brand mijia">${mijiaIcon()}</div>
            <div class="cell-content">
              <span class="cell-title">米家</span>
              <span class="cell-subtitle">${credentialsInvalid ? "登录已失效，设备配置已保留" : "连接异常，设备配置已保留"}</span>
            </div>
          </div>
          <div class="cell provider-actions">
            ${credentialsInvalid
              ? `<button class="button primary" id="btn-qr-login">重新登录</button>
                 <button class="button plain" id="btn-other-login">登录其他账号</button>`
              : `<button class="button primary" id="btn-retry-provider">重试连接</button>
                 <button class="button plain" id="btn-other-login">重新登录</button>`}
          </div>
        </div>
        ${credentialsInvalid ? `<p class="section-note">重新登录同一账号会恢复原配置；登录其他账号后，旧账号的设备、入口和分组将被清理。</p>` : ""}
      `;
    }
    return `
      <div class="page-heading">
        <h2 class="page-title">服务</h2>
        <p>连接音箱品牌服务，自动同步可用设备。</p>
      </div>

      <div class="group-header">已连接</div>
      <div class="group">
        <div class="cell provider-row">
          <div class="cell-icon device-brand mijia">${mijiaIcon()}</div>
          <div class="cell-content">
            <span class="cell-title">米家</span>
            <span class="cell-subtitle">已连接${state.devices.length ? ` · ${state.devices.length} 台音箱` : ""}</span>
          </div>
          <button class="button plain danger-text provider-disconnect" id="btn-logout">断开</button>
        </div>
      </div>
    `;
  }

  return `
    <div class="page-heading">
      <h2 class="page-title">服务</h2>
      <p>连接音箱品牌服务，自动同步可用设备。</p>
    </div>

    <div class="group-header">可连接</div>
    <div class="group">
      <div class="cell clickable" id="btn-qr-login">
        <div class="cell-icon device-brand mijia">${mijiaIcon()}</div>
        <div class="cell-content">
          <span class="cell-title">米家</span>
          <span class="cell-subtitle">使用米家 App 扫码连接</span>
        </div>
        <span class="caption">›</span>
      </div>
    </div>

    <button class="button plain advanced-login-toggle" id="btn-cookie-login">使用凭据连接</button>

    <div id="cookie-form" style="display: none;">
      <div class="group-header">连接凭据</div>
      <div class="group">
        <div class="cell" style="flex-direction: column; align-items: stretch; gap: var(--space-md); padding: var(--space-lg);">
          <input type="text" id="cookie-user-id" placeholder="userId" class="input">
          <input type="text" id="cookie-pass-token" placeholder="passToken" class="input">
          <button class="button primary full" id="btn-cookie-submit">连接</button>
          <button class="button plain" id="btn-cookie-cancel">取消</button>
        </div>
      </div>
    </div>
  `;
}

export function bindAccountView(
  container: HTMLElement,
  handlers: {
    onBack: () => void;
    onLogout: () => void;
    onQRLogin: () => void;
    onRetry: () => void;
    onCookieLogin: (userId: string, passToken: string) => void;
  }
) {
  container.querySelector("[data-account-back]")?.addEventListener("click", handlers.onBack);
  container.querySelector("#btn-logout")?.addEventListener("click", handlers.onLogout);
  container.querySelector("#btn-qr-login")?.addEventListener("click", handlers.onQRLogin);
  container.querySelector("#btn-other-login")?.addEventListener("click", handlers.onQRLogin);
  container.querySelector("#btn-retry-provider")?.addEventListener("click", handlers.onRetry);

  const cookieBtn = container.querySelector("#btn-cookie-login");
  const cookieForm = container.querySelector("#cookie-form");
  cookieBtn?.addEventListener("click", () => {
    if (cookieForm) {
      (cookieForm as HTMLElement).style.display = "block";
    }
  });

  container.querySelector("#btn-cookie-cancel")?.addEventListener("click", () => {
    if (cookieForm) {
      (cookieForm as HTMLElement).style.display = "none";
    }
  });

  container.querySelector("#btn-cookie-submit")?.addEventListener("click", () => {
    const userId = (container.querySelector("#cookie-user-id") as HTMLInputElement)?.value;
    const passToken = (container.querySelector("#cookie-pass-token") as HTMLInputElement)?.value;
    if (!userId || !passToken) {
      // handled by caller
      return;
    }
    handlers.onCookieLogin(userId, passToken);
  });
}
