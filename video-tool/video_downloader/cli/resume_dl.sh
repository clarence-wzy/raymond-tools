#!/usr/bin/env bash
# 仅用 ffmpeg/ffprobe 的“时间级断点续传”下载器（命令行参考实现）
set -u
FF="${FFMPEG:-ffmpeg}"
FP="${FFPROBE:-ffprobe}"
URL="${1:-}"
OUT="${2:-out.mp4}"

if [ -z "$URL" ]; then
  echo "用法：$0 <URL> [输出文件]" >&2
  exit 2
fi
command -v "$FF" >/dev/null 2>&1 || { echo "未找到 ffmpeg：$FF" >&2; exit 1; }
command -v "$FP" >/dev/null 2>&1 || { echo "未找到 ffprobe：$FP" >&2; exit 1; }

WORK="${OUT%.*}.parts"
mkdir -p "$WORK"
if pwd -W >/dev/null 2>&1; then
  WORK=$(cd "$WORK" && pwd -W)
else
  WORK=$(cd "$WORK" && pwd)
fi

HTTP_OPTS=()
HLS_OPTS=()
case "$URL" in
  http://*|https://*) HTTP_OPTS=(-reconnect 1 -reconnect_streamed 1 -reconnect_on_network_error 1 -reconnect_max_retries 10 -reconnect_delay_max 10) ;;
esac
case "$URL" in
  *.m3u8*|*.m3u*|*m3u8*) HLS_OPTS=(-seg_max_retry 10 -max_reload 100) ;;
esac

start_time() { "$FP" -v error -show_entries format=start_time -of csv=p=0 "$1" 2>/dev/null; }
duration()   { "$FP" -v error -show_entries format=duration   -of csv=p=0 "$1" 2>/dev/null; }

# 用下一段的实际起点回裁上一段的尾巴，消除 seek 落点造成的重叠/损坏包
trim_to() {
  local prev="$1" cut="$2" ps pd want
  ps=$(start_time "$prev"); pd=$(duration "$prev")
  [ -z "$ps" ] && ps=0
  [ -z "$pd" ] && pd=0
  want=$(awk -v c="$cut" -v s="$ps" 'BEGIN{printf "%.3f", c-s}')
  if awk -v w="$want" -v d="$pd" 'BEGIN{exit !(w>0.05 && w<d-0.001)}'; then
    "$FF" -hide_banner -loglevel error -y -i "$prev" -t "$want" -c copy "$prev.tmp.ts" \
      && mv -f "$prev.tmp.ts" "$prev"
  fi
}

SRC_START=$(start_time "$URL"); [ -z "$SRC_START" ] && SRC_START=0
resume=$SRC_START
prev=""
n=0
for p in "$WORK"/part_*.ts; do
  [ -e "$p" ] || continue
  s=$(start_time "$p"); d=$(duration "$p")
  [ -z "$s" ] && s=0
  [ -z "$d" ] && d=0
  resume=$(awk -v s="$s" -v d="$d" 'BEGIN{printf "%.3f", s+d}')
  prev="$p"
  n=$((n+1))
done
echo "已有 $n 段，从源时间轴 ${resume}s 继续" >&2

SAFETY="${SAFETY:-2}"
fails=0
while :; do
  part=$(printf "%s/part_%03d.ts" "$WORK" "$n")
  seek_abs=$(awk -v a="$resume" -v s="$SAFETY" 'BEGIN{v=a-s; if(v<0)v=0; printf "%.3f", v}')
  rel=$(awk -v a="$seek_abs" -v s="$SRC_START" 'BEGIN{v=a-s; if(v<0)v=0; printf "%.3f", v}')
  "$FF" -hide_banner -nostdin -loglevel warning -xerror \
    -rw_timeout 15000000 "${HTTP_OPTS[@]}" "${HLS_OPTS[@]}" \
    -stats_period 5 -progress pipe:1 -nostats \
    -copyts -ss "$rel" -i "$URL" -c copy -f mpegts "$part" \
    > "$WORK/progress.log" 2> "$WORK/ffmpeg.err.log"
  rc=$?

  s=$(start_time "$part"); d=$(duration "$part")
  if [ -n "${d:-}" ] && awk -v d="$d" 'BEGIN{exit !(d>0.5)}'; then
    if [ -n "$prev" ] && [ -e "$prev" ]; then trim_to "$prev" "$s"; fi
    prev="$part"
    resume=$(awk -v s="${s:-0}" -v d="$d" 'BEGIN{printf "%.3f", s+d}')
    n=$((n+1))
    fails=0
    [ "$rc" -eq 0 ] && break
  else
    rm -f "$part"
    [ "$rc" -eq 0 ] && break
    fails=$((fails+1))
    if [ "$fails" -ge 10 ]; then
      echo "连续失败 $fails 次，放弃；详见 $WORK/ffmpeg.err.log" >&2
      exit 1
    fi
    sleep 5
  fi
done

: > "$WORK/list.txt"
for p in "$WORK"/part_*.ts; do [ -e "$p" ] && echo "file '$p'" >> "$WORK/list.txt"; done
"$FF" -hide_banner -loglevel warning -f concat -safe 0 -i "$WORK/list.txt" -c copy -movflags +faststart "$OUT"
