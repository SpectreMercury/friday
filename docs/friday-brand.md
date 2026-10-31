# Friday 品牌

2026-09-27，用户明确选定并要求使用上传的白底黑色线条 AI 面甲图作为产品 Logo。

## 正式资源

- 用户原图：`docs/brand/friday-user-selected.png`，1254×1254 PNG，原样保留。
- 网页主图：`local_ai/pwa/friday-icon.png`，与上传原图字节一致。
- PWA 图标：`friday-icon-192.png`、`friday-icon-512.png`。
- Apple Touch Icon：`friday-apple-touch-icon.png`，180×180。
- 网页标题、登录页、离线页和助手头像继续引用同一套 PNG 资源。
- iPhone App 图标：`ios/Harness/Resources/Assets.xcassets/AppIcon.appiconset/AppIcon.png`，1024×1024。
- iPhone 配对页：`FridayLogo.imageset/FridayLogo.png`，512×512。

仅用 macOS sips 缩放导出各尺寸，保留白底、颜色、线条、留白和完整画面，未使用图片模型重新生成或改画。`icon.svg` 沿用现有兼容入口，只嵌入新 192 PNG；其中没有描摹路径。

Service Worker 缓存版本为 `harness-public-v17`，激活时清理旧公共缓存。PWA 重新打开后加载新资源；系统主屏幕图标的刷新受系统缓存影响。原生 App 需重新构建和安装才能更新已安装图标。

替换前资源与说明备份在 `docs/brand/before-user-selected-20260927/`。`docs/brand/concepts/` 中此前生成的图片都是探索稿，不再代表正式 Logo。
