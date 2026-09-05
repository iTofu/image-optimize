# image-optimize

[English](../README.md) | 简体中文

把 Figma 等工具导出的 SVG / PNG 整理成任何渲染引擎都画得对的形态，尤其是 iOS asset catalog 用的 CoreSVG。作为 [Claude Code](https://claude.com/claude-code) skill 安装后，agent 会在素材进项目时自动调用；`svg_optimize.py` 也可以脱离 agent 当命令行工具单独用。

## 为什么需要它

CoreSVG（Xcode asset catalog 渲染 SVG 的引擎）对 SVG 的支持是个子集：不认 `mask-type:alpha`、不认 `style="mask:…"` 和带引号的 `url('#id')`、遮罩边缘发虚、描边偶尔画错。Figma 导出的图标恰好大量使用这些写法，结果就是「设计稿没问题、真机上图标糊 / 缺一块 / 整个不显示」。

本工具把这些结构在离线阶段变成纯几何：

| 输入里的写法 | 处理 |
|---|---|
| `<mask>`（硬遮罩） | 遮罩下每个图形在自己的坐标系里与遮罩求交，**原位替换**为 `<path>`：图层顺序、祖先 transform、分组 opacity、继承的 fill 全部保留 |
| `stroke`（含从 `<g>` 继承的） | 描边求轮廓变成填充 `<path>`；既有填充又有描边的图形保留填充、紧随其后补一条描边路径（原来半透明的话包在 `<g opacity>` 里，合成结果不变） |
| `var(--name, #color)` | 解析成实际颜色 |
| `width="100%"` / `height="100%"` | 换成 viewBox 尺寸 |
| `<rect>` `<circle>` `<ellipse>` `<line>` `<polygon>` `<polyline>` | 转成 `<path>`，其余属性原样保留 |
| `<use>` | 就地展开（含 SVG 2 的 `href`） |
| 画不出东西的元素 | 删除 |

处理完的文件只剩 `<path>` 加 `fill`，哪个引擎都一样。对输出再跑一次，字节相同。

## 它拒绝猜的情形

做不成硬几何的结构**原样保留**（`<mask>` 定义留着、元素保留 `mask` 属性），在 stderr 打 `[WARN]`，对应的 `[OK]` 行带 `masks-left:N`。这时该回设计源头改，而不是让工具瞎猜：

- 软遮罩：遮罩内容有 `opacity` / `fill-opacity`（含继承的）、非纯黑白的填充、带 alpha 的颜色
- 遮罩内容或被遮罩内容里有 `<image>`、`<text>`、嵌套 `<svg>`、`clip-path`、`filter`；`<use>` 只在指向文件外时保留
- 内层展不平的遮罩：外层也整个保留，两层都不丢
- 按包围盒布局的渐变 / pattern 填充（`gradientUnits` 不是 `userSpaceOnUse`；Figma 导出总是 userSpaceOnUse）
- `maskContentUnits="objectBoundingBox"`
- 虚线描边、`vector-effect` 描边、带 `mask` / `clip-path` / `filter` 的描边图形：描边保留不转
- 祖先的 clip-path / mask / filter 按 objectBoundingBox 布局（mask 与 filter 区域的默认单位；Figma 导出会显式写 userSpaceOnUse）：改后代几何会让它移位，保留不动
- 带 marker 的图形：新几何的顶点已不是原路径的，保留不动
- 文件里有 `<style>` 样式表且设置了绘制、效果或 marker 属性：脚本不做 CSS 选择器匹配，整份文件的遮罩与描边都保留不动

非法文件（不是 SVG、解析失败、管线内任何异常）打 `[FAIL]` 并跳过，输入不动，批次其余文件照常处理，最后退出码 1。

## 安装

依赖：Python 3 + [picosvg](https://github.com/googlefonts/picosvg)（SVG 管线）、[pngquant](https://pngquant.org/)（PNG 管线），两条管线互不阻塞。

```bash
pip3 install picosvg
brew install pngquant

git clone https://github.com/iTofu/image-optimize.git
cd image-optimize && ./install.sh
```

`install.sh` 把 `skill/` 软链到 `~/.claude/skills/image-optimize`（目标已存在且不是软链时拒绝覆盖），顺带检查依赖，缺什么只提示不代装。升级在仓库里 `git pull` 即生效；卸载运行 `./uninstall.sh`。

不用 Claude Code 也能用：把 `skill/svg_optimize.py` 拷走就是一个单文件脚本。

## 使用

### 作为 Claude Code skill

装好后不需要手动触发。skill 的描述要求 agent 在任何 `*.svg` / `*.png` 写入项目路径（尤其 `.xcassets/`）之后、引用它之前先跑一遍优化，所以从 Figma 拉图标、用户丢图进来、下载素材，都会自动过这一道。规则细节在 `skill/SKILL.md`。

### 命令行

```bash
S=~/.claude/skills/image-optimize/svg_optimize.py

python3 $S icon.svg -o icon.svg              # 单文件原位覆盖
python3 $S *.svg --in-place                  # 批量原位（推荐）
python3 $S *.svg --outdir cleaned/           # 保留原件
python3 $S icon.svg -o icon.svg --no-outline-stroke   # 装饰性插画不转描边

pngquant *.png --ext=.png --force --skip-if-larger
```

批量模式会检查同一目录（iOS asset catalog 则是 `.imageset` 的上一级）里的 SVG viewBox 尺寸是否一致：并排显示的一组图标应当共用一个尺寸，不一致通常意味着有一张导出的是错的 Figma 图层。`--no-sibling-check` 可关闭。

每个文件输出一行：

```
[OK] icon.svg -> icon.svg  masks:2 strokes:3 size:1532→1210B
[OK] badge.svg -> badge.svg  masks:0 masks-left:1 strokes:0 size:804→806B
[FAIL] broken.svg: XMLSyntaxError: Document is empty, line 1, column 1
```

## 测试

两层。第一层不依赖渲染器，任何机器秒级跑完，CI 对每个 PR 必跑；第二层用真实渲染引擎回答「画出来是否一样」，本地跑。

### 快测层（pytest）

```bash
pip3 install -r tests/requirements.txt
pytest
```

- `tests/test_golden.py`：`tests/cases/` 里每个用例的输出、`[WARN]` 与统计行必须与 `tests/expected/` 记录的一致（结构比对，忽略属性顺序与格式，数值容差 0.001）；对输出再跑一次必须字节相同；没有任何 WARN 的用例，输出里不得再有 `<mask>`、基本图形、`<use>` 或任何描边。
- `tests/test_cli.py`：命令行契约，退出码、`[OK]` / `[FAIL]` / `[SKIP]` 行、批量遇错继续、sibling viewBox 检查。
- `tests/test_units.py`：那些决定「碰不碰」的纯函数：颜色 alpha、paint-order、marker、bbox 布局的 clip / mask / filter、样式表属性收集、CSS var、尺寸修正。

有意改变输出时跑 `python3 tests/update_expected.py`（可跟用例名只更新部分），把 `tests/expected/` 的 diff 放进 PR：那个 diff 就是行为变化本身。`tests/requirements.txt` 钉死了 picosvg 与 skia-pathops 版本，否则路径布尔运算的浮点尾数会让 expected 在不同机器上漂。

`tests/svgcmp.py` 也可单独用来比两个文件或两个目录：`python3 tests/svgcmp.py a/ b/`。

### 渲染层（harness）

```bash
tests/run.sh            # CoreSVG 单引擎，秒级：无意外崩溃 + 输出幂等
tests/run.sh --chrome   # 加 Chrome 无头做双引擎像素比对，约 7 秒一个用例
```

harness 用两个引擎分别渲染原图和优化图，算像素差异率。Chrome 是判定引擎（原图 vs 优化图差异超过 1% 即失败）；CoreSVG 只作参考，因为它对原图本身就画错的那几个用例正是这个工具存在的理由。`tests/rendersvg` 首次运行时由 `tests/render.swift` 编出，需要 Xcode 命令行工具；`--chrome` 需要本机装有 Google Chrome。Chrome 无头一次只能跑一个实例。

### 用例

`tests/cases/` 按前缀分组：

- `c*` 遮罩展平的各种结构：图层顺序、transform、继承样式、嵌套遮罩、evenodd、共享遮罩、单位、遮罩区域裁剪
- `r*` 健壮性输入：空文件、非 SVG、无 viewBox、巨大坐标、注释与处理指令、嵌套 svg
- `x*` / `y*` / `z*` / `w*` 边界情形：继承 stroke、半透明填充加描边、bbox 渐变、内层软遮罩、宿主上的 clip-path / filter、零宽描边、遮罩继承透明度、祖先 bbox 效果、paint-order、marker、样式表

新发现的画错情形先加一个最小 SVG 用例复现（用 `--chrome` 看到红），再修，修完 `update_expected.py` 记录输出。

## 许可

[Apache License 2.0](../LICENSE)。
