from whisper_key.hardware_probe import Hardware, recommend


def hw(**kw):
    base = dict(gpu_class=None, gpu_name=None, vram_gb=None, has_vulkan=False, cpu_count=8, ram_gb=16.0)
    base.update(kw)
    return Hardware(**base)


def test_big_nvidia_gets_turbo_on_cuda():
    r = recommend(hw(gpu_class="nvidia", gpu_name="RTX 5090", vram_gb=32.0))
    assert (r["model"], r["runtime"], r["voiceAdvised"]) == ("large-v3-turbo", "cuda", True)
    assert r["summary"].startswith("RTX 5090, 32 GB VRAM")


def test_small_nvidia_gets_a_smaller_model():
    assert recommend(hw(gpu_class="nvidia", gpu_name="GTX 1050", vram_gb=2.0))["model"] == "small"


def test_other_gpu_with_vulkan_uses_whisper_cpp():
    r = recommend(hw(gpu_class="amd_rdna2+", gpu_name="Radeon RX 7800", has_vulkan=True))
    assert (r["model"], r["runtime"]) == ("small", "vulkan")


def test_cpu_only_scales_with_cores_and_weak_machines_are_told_honestly():
    assert recommend(hw(cpu_count=8))["model"] == "small"
    assert recommend(hw(cpu_count=4))["model"] == "base"
    weak = recommend(hw(cpu_count=2))
    assert weak["voiceAdvised"] is False and weak["model"] is None
    assert recommend(hw(gpu_class="nvidia", gpu_name="x", vram_gb=24.0, ram_gb=2.0))["voiceAdvised"] is False
