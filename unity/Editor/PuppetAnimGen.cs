// 木偶轴姿势片段生成器 —— 复制到 Unity 工程的 Assets/Editor/ 下,
// 菜单 Tools → Anima → 生成木偶姿势片段。
//
// 为什么用脚本:人形(Humanoid)骨架由 Animator 的 muscle 系统接管,
// 场景里直接旋转骨骼在录制模式下会被弹回(Unity 已知限制);姿势的
// 正确形态是 muscle 曲线,而 Animation 窗口手拨滑条有近百个。这里
// 每个姿势只需 2~3 条曲线,直接写出最干净。
//
// 方向/幅度不对:改下面 Poses 表里的数值(-1~1),再点一次菜单——
// 原地覆盖、GUID 不变,blend tree 里的引用不会丢。
//
// 数值约定:同一轴的 Min/Max 两端严格对称(取负),这样轴=0 时所有
// muscle 恰好回 0。注意手臂轴 0 = T 姿平举(muscle 0 的天然含义),
// 自然垂手在轴 = -1,所以 Expression Parameters 里 ArmUp 默认值填 -1。

using System.Collections.Generic;
using UnityEditor;
using UnityEngine;

public static class PuppetAnimGen
{
    const string OutDir = "Assets/Anima/PuppetAnims";

    // 每个姿势片段:muscle 名(必须与 Unity HumanTrait 完全一致)→ 值
    static readonly Dictionary<string, Dictionary<string, float>> Poses = new()
    {
        // 躯干左右倾(+1 = 右倾)
        ["Pose_LeanX_L"] = new()
        {
            ["Spine Left-Right"] = -0.40f,
            ["Chest Left-Right"] = -0.30f,
        },
        ["Pose_LeanX_R"] = new()
        {
            ["Spine Left-Right"] = 0.40f,
            ["Chest Left-Right"] = 0.30f,
        },
        // 躯干前后倾(+1 = 前倾;Unity 命名 Front-Back 的正方向是 Back)
        ["Pose_LeanZ_B"] = new()
        {
            ["Spine Front-Back"] = 0.40f,
            ["Chest Front-Back"] = 0.30f,
        },
        ["Pose_LeanZ_F"] = new()
        {
            ["Spine Front-Back"] = -0.40f,
            ["Chest Front-Back"] = -0.30f,
        },
        // 左臂抬起(-1 = 垂下,0 = 平举,+1 = 举过头)
        ["Pose_ArmL_Up_Min"] = new()
        {
            ["Left Arm Down-Up"] = -1.00f,
            ["Left Shoulder Down-Up"] = -0.40f,
        },
        ["Pose_ArmL_Up_Max"] = new()
        {
            ["Left Arm Down-Up"] = 1.00f,
            ["Left Shoulder Down-Up"] = 0.40f,
        },
        // 右臂抬起
        ["Pose_ArmR_Up_Min"] = new()
        {
            ["Right Arm Down-Up"] = -1.00f,
            ["Right Shoulder Down-Up"] = -0.40f,
        },
        ["Pose_ArmR_Up_Max"] = new()
        {
            ["Right Arm Down-Up"] = 1.00f,
            ["Right Shoulder Down-Up"] = 0.40f,
        },
        // 左臂前伸(+1 = 前平举;Front-Back 正方向是 Back,故前伸取负)
        ["Pose_ArmL_Fwd_Min"] = new()
        {
            ["Left Arm Front-Back"] = 0.70f,
            ["Left Shoulder Front-Back"] = 0.25f,
        },
        ["Pose_ArmL_Fwd_Max"] = new()
        {
            ["Left Arm Front-Back"] = -0.70f,
            ["Left Shoulder Front-Back"] = -0.25f,
        },
        // 右臂前伸
        ["Pose_ArmR_Fwd_Min"] = new()
        {
            ["Right Arm Front-Back"] = 0.70f,
            ["Right Shoulder Front-Back"] = 0.25f,
        },
        ["Pose_ArmR_Fwd_Max"] = new()
        {
            ["Right Arm Front-Back"] = -0.70f,
            ["Right Shoulder Front-Back"] = -0.25f,
        },
    };

    [MenuItem("Tools/Anima/生成木偶姿势片段")]
    public static void Generate()
    {
        var valid = new HashSet<string>(HumanTrait.MuscleName);
        System.IO.Directory.CreateDirectory(OutDir);
        int written = 0, bad = 0;
        foreach (var pose in Poses)
        {
            var clip = new AnimationClip { name = pose.Key };
            foreach (var muscle in pose.Value)
            {
                if (!valid.Contains(muscle.Key))
                {
                    Debug.LogError(
                        $"[Anima] muscle 名不存在:\"{muscle.Key}\"" +
                        "(Unity 版本差异?把 Console 这行发回去)");
                    bad++;
                    continue;
                }
                clip.SetCurve(
                    "", typeof(Animator), muscle.Key,
                    AnimationCurve.Constant(0f, 1f / 60f, muscle.Value));
            }
            var path = $"{OutDir}/{pose.Key}.anim";
            var existing = AssetDatabase.LoadAssetAtPath<AnimationClip>(path);
            if (existing != null)
                EditorUtility.CopySerialized(clip, existing); // 保 GUID,引用不丢
            else
                AssetDatabase.CreateAsset(clip, path);
            written++;
        }
        AssetDatabase.SaveAssets();
        AssetDatabase.Refresh();
        Debug.Log($"[Anima] 已生成 {written} 个姿势片段到 {OutDir}"
                  + (bad > 0 ? $",{bad} 条 muscle 名没认出,见上方红字" : ""));
    }
}
