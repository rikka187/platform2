"""把 YL 文字标换成新的冷链 logo：生成图片 + 替换页面与侧栏标记。"""
import pathlib
from PIL import Image

ROOT = pathlib.Path(r'D:\platform2\platform_v1')
STATIC = ROOT / 'app' / 'static'
SRC = pathlib.Path(r'C:\Users\rikka\.dsh\attachments\v1\objects\7e'
                   r'\7eb2b1e44e0ac70f14443a5c49995a3d4c513dedb69628a09e77773f684b4cca')

# ---------- 1. 生成 logo 与 favicon ----------
image = Image.open(SRC).convert('RGBA')
# 内容裁掉四周留白后放到白色圆角底上，保证在深色侧栏与浅色页面上都清晰
bbox = image.convert('RGB').point(lambda value: 255 if value > 246 else 0).convert('L').getbbox()
if bbox:
    image = image.crop(bbox)
side = max(image.size)
canvas = Image.new('RGBA', (side, side), (255, 255, 255, 0))
canvas.paste(image, ((side - image.width) // 2, (side - image.height) // 2))
logo = canvas.resize((256, 256), Image.LANCZOS)
logo_path = STATIC / 'logo.png'
logo.save(logo_path, 'PNG', optimize=True)
print(f'已生成 {logo_path.name} {logo_path.stat().st_size / 1024:.1f} KB {logo.size}')

icon = canvas.resize((64, 64), Image.LANCZOS)
icon_path = STATIC / 'favicon.png'
icon.save(icon_path, 'PNG', optimize=True)
print(f'已生成 {icon_path.name} {icon_path.stat().st_size / 1024:.1f} KB {icon.size}')

# ---------- 2. 侧栏与登录页的 YL 标记 ----------
shell = STATIC / 'shell.js'
text = shell.read_text(encoding='utf-8')
old = '<div class="mark">YL</div>'
new = '<img class="mark-logo" src="/logo.png" alt="银犁物流" title="银犁物流城市冷链仓配一体化协同平台">'
if old in text:
    shell.write_text(text.replace(old, new), encoding='utf-8')
    print('shell.js：侧栏标记已换为 logo 图片')
else:
    print('shell.js：未找到 YL 标记')

login = STATIC / 'login.html'
text = login.read_text(encoding='utf-8')
if old in text:
    text = text.replace(old, new, 1)
    text = text.replace(
        "  .login-intro .mark{width:56px;height:56px;border-radius:16px;background:linear-gradient(135deg,#45d0c5,#7dc7ff);\n"
        "    display:grid;place-items:center;font-weight:900;color:#0f2740;font-size:20px}",
        "  .login-intro .mark-logo{width:64px;height:64px;border-radius:18px;background:#fff;padding:6px;\n"
        "    object-fit:contain;box-shadow:0 10px 24px rgba(6,20,34,.28)}")
    login.write_text(text, encoding='utf-8')
    print('login.html：登录页标记已换为 logo 图片')

# ---------- 3. 公共样式 ----------
css = STATIC / 'admin.css'
text = css.read_text(encoding='utf-8')
if 'mark-logo' not in text:
    text += """

/* 品牌标识（使用 logo 图片替代文字标记） */
.shell .side .brand img.mark-logo{width:46px;height:46px;border-radius:13px;background:#fff;padding:4px;
  object-fit:contain;flex:none;box-shadow:0 6px 16px rgba(6,20,34,.25)}
"""
    css.write_text(text, encoding='utf-8')
    print('admin.css：已加 logo 样式')

# ---------- 4. 全站 favicon 指向 PNG ----------
count = 0
for page in STATIC.glob('*.html'):
    text = page.read_text(encoding='utf-8')
    if 'href="/favicon.svg"' in text:
        page.write_text(text.replace('<link rel="icon" type="image/svg+xml" href="/favicon.svg">',
                                      '<link rel="icon" type="image/png" href="/favicon.png">'), encoding='utf-8')
        count += 1
print(f'favicon 已切换到 PNG：{count} 个页面')
