#!/bin/bash

# Batch frame extraction for all training videos

VIDEO_DIR="training data"
OUTPUT_BASE="frame_extraction_test"

# Video-config pairs
declare -A VIDEOS
VIDEOS["崩坏三舰长线全剧情合集，第一节_（仲夏幻夜） - 1.舰长月下观星丽塔线主线全剧情，第一节（仲夏幻夜）(Av576535774,P1).mp4"]="tools/configs/yuexia_ep01_roi.yaml"
VIDEOS["崩坏三舰长线全剧情合集第十七节_（在长梦弥散之前） - 1.崩坏三舰长线全剧情合集第十六节_（在长梦弥散之前）(Av407059874,P1).mp4"]="tools/configs/yuexia_ep17_roi.yaml"
VIDEOS["崩坏三舰长线全剧情合集第十八节第一部分_（当红月落幕之后） - 1.崩坏三舰长线全剧情合集第十七节_（当红月落幕之后）(Av789670799,P1).mp4"]="tools/configs/yuexia_ep18p1_roi.yaml"
VIDEOS["崩坏三舰长线全剧情合集第十八节第二部分_（当红月落幕之后） - 1.崩坏三舰长线全剧情合集第十七节第二部分_（当红月落幕之后）(Av959708444,P1).mp4"]="tools/configs/yuexia_ep18p2_roi.yaml"
VIDEOS["崩坏三舰长线全剧情合集第十八节第三部分_（当红月落幕之后） - 1.崩坏三舰长线全剧情合集第十七节第三部分_（当红月落幕之后）(Av277126645,P1).mp4"]="tools/configs/yuexia_ep18p3_roi.yaml"
VIDEOS["崩坏三舰长线全剧情合集第十八节主要支线一月下全回忆和彩蛋_（当红月落幕之后） - 1.崩坏三舰长线全剧情合集第十七节第四部分，月下全回忆和彩蛋_（当红月落幕之后）(Av620480403,P1).mp4"]="tools/configs/yuexia_ep18side_roi.yaml"
VIDEOS["崩坏三舰长线全剧情合集第十九节（最后一节）_舰舰的牧场奇谭（附邀约和彩蛋） - 1.崩坏三舰长线全剧情合集第十八节（最后一节）_舰舰的牧场奇谭（附邀约彩蛋）(Av578595951,P1).mp4"]="tools/configs/yuexia_ep19_roi.yaml"

echo "=== Batch Frame Extraction ==="
echo "Total videos: ${#VIDEOS[@]}"
echo ""

for video in "${!VIDEOS[@]}"; do
    config="${VIDEOS[$video]}"
    video_name=$(basename "$video" .mp4)
    output_dir="$OUTPUT_BASE/${video_name:0:40}"

    echo "Processing: ${video_name:0:60}..."
    echo "  Config: $config"
    echo "  Output: $output_dir"

    python3 -m tools.frame_extractor "$VIDEO_DIR/$video" "$config" \
        --output-dir "$output_dir" --fps 2.0 2>&1 | tail -5

    if [ $? -eq 0 ]; then
        event_count=$(ls "$output_dir/frames/" 2>/dev/null | wc -l)
        echo "  ✓ Extracted $event_count events"
    else
        echo "  ✗ Failed"
    fi
    echo ""
done

echo "=== Summary ==="
for dir in "$OUTPUT_BASE"/*; do
    if [ -d "$dir/frames" ]; then
        count=$(ls "$dir/frames/" | wc -l)
        echo "$(basename "$dir"): $count events"
    fi
done
