const button = document.querySelector('#connect'), status = document.querySelector('#status');
button.addEventListener('click', async () => {
  button.disabled = true;
  try {
    const sessionResponse = await fetch('/v1/web/auth/session', {credentials:'same-origin',cache:'no-store'});
    if (!sessionResponse.ok) throw new Error('登录已过期，请返回 App 重新登录。');
    const session = await sessionResponse.json();
    const response = await fetch('/v1/native/auth/authorize', {method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRF-Token':session.csrf_token},body:JSON.stringify({flow:new URL(location.href).searchParams.get('flow')})});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error?.message || '连接未完成，请重试。');
    const url = new URL(result.url);
    if (url.protocol !== 'friday-private:' || url.hostname !== 'auth' || url.pathname !== '/callback') throw new Error('回调地址无效。');
    status.textContent='正在返回 Friday…'; location.assign(url.href);
  } catch (error) { status.textContent=error.message; button.disabled=false; }
});
