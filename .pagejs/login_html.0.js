
const form = document.getElementById('loginForm');
const errorBox = document.getElementById('loginError');
const submitBtn = document.getElementById('submitBtn');

function showError(message) {
  errorBox.textContent = message;
  errorBox.style.display = 'block';
}

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  errorBox.style.display = 'none';
  submitBtn.disabled = true;
  submitBtn.textContent = '登录中…';
  try {
    const response = await fetch('/api/auth/login', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        username: document.getElementById('username').value.trim(),
        password: document.getElementById('password').value
      })
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      showError(data.error || `登录失败（HTTP ${response.status}）`);
      return;
    }
    const next = new URLSearchParams(location.search).get('next');
    location.replace(next && next.startsWith('/') ? next : '/');
  } catch (e) {
    showError('网络异常：' + e.message);
  } finally {
    submitBtn.disabled = false;
    submitBtn.textContent = '登 录';
  }
});

// 已登录则直接进入驾驶舱
fetch('/api/auth/me', { credentials: 'same-origin' }).then(r => {
  if (r.ok) location.replace('/');
}).catch(() => {});
