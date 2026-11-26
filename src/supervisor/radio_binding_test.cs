// Q71: offline checks for RadioBinding multi-adapter matching.
using System;

internal static class RadioBindingTest
{
    public static int Main()
    {
        int failures = 0;

        RadioTarget c820 = RadioBinding.MatchTarget(@"USB\VID_0BDA&PID_C820&MI_02\7&123456&0&0002");
        Check(ref failures, c820 != null && c820.PidHex == "0xc820" && c820.Name == "RTL8821CU",
            "8821CU MI_02 matches");

        Check(ref failures, RadioBinding.MatchTarget(@"USB\VID_0BDA&PID_C820&MI_00\7&123456&0&0000") == null,
            "8821CU MI_00 Bluetooth must not match");
        Check(ref failures, RadioBinding.MatchTarget(@"USB\VID_0BDA&PID_C820\123456") == null,
            "8821CU composite parent must not match");

        RadioTarget c811 = RadioBinding.MatchTarget(@"usb\vid_0bda&pid_c811\123456");
        Check(ref failures, c811 != null && c811.PidHex == "0xc811" && c811.Name == "RTL8811CU",
            "8811CU non-composite matches case-insensitively");

        Check(ref failures, RadioBinding.MatchTarget(@"USB\VID_0BDA&PID_C811&MI_00\123456") == null,
            "8811CU MI_00 must not match non-composite rule");
        Check(ref failures, RadioBinding.MatchTarget(@"USB\VID_0BDA&PID_1A2B\123456") == null,
            "ZeroCD 1A2B must not match");
        Check(ref failures, RadioBinding.MatchTarget(null) == null, "null rejected");
        Check(ref failures, RadioBinding.MatchTarget("") == null, "empty rejected");

        Check(ref failures, RadioBinding.Bound("WinUSB"), "WinUSB bound");
        Check(ref failures, RadioBinding.Bound("winusb"), "winusb case-insensitive");
        Check(ref failures, !RadioBinding.Bound("RtlWlanu"), "RtlWlanu not bound");
        Check(ref failures, !RadioBinding.Bound(""), "empty service not bound");

        Check(ref failures, RadioBinding.AssessNetworkRisk(true, false), "target up alone -> high risk");
        Check(ref failures, !RadioBinding.AssessNetworkRisk(true, true), "target up with backup -> not sole network");
        Check(ref failures, !RadioBinding.AssessNetworkRisk(false, false), "target down -> no disconnect risk");

        Check(ref failures, RadioBinding.IsTargetAdapterDescription("Realtek 8811CU Wireless LAN 802.11ac USB NIC"),
            "matches 8811CU description");
        Check(ref failures, RadioBinding.IsTargetAdapterDescription("Realtek 8821CU Wireless LAN 802.11ac USB NIC"),
            "matches 8821CU description");
        Check(ref failures, !RadioBinding.IsTargetAdapterDescription("Intel(R) Wi-Fi 7 BE200 320MHz"),
            "does not match Intel Wi-Fi");

        string samplePnp =
            "Published Name : oem12.inf\r\n" +
            "Original Name : other.inf\r\n" +
            "Published Name : oem45.inf\r\n" +
            "Original Name : airbridge-winusb.inf\r\n" +
            "发布名称 : oem99.inf\r\n" +
            "原始名称 : airbridge-winusb.inf\r\n";
        var parsed = RadioBinding.ParsePublishedInfNames(samplePnp, "airbridge-winusb.inf");
        Check(ref failures, parsed.Count == 2 && parsed[0] == "oem45.inf" && parsed[1] == "oem99.inf",
            "parses English and localized pnputil output");

        // Q74: send URI roundtrip, JSON helper, and file validation
        string sendUri = Activation.BuildSendUri(new[] { @"C:\tmp\a + b.jpg", @"D:\测试\c.mov" });
        Check(ref failures, Activation.IsSendUri(sendUri), "IsSendUri detects send protocol");
        string[] roundtrip = Activation.FilesFromSendUri(sendUri);
        Check(ref failures, roundtrip.Length == 2 && roundtrip[0] == @"C:\tmp\a + b.jpg" && roundtrip[1] == @"D:\测试\c.mov",
            "send URI roundtrips spaces, pluses, and Unicode paths");

        string json = "{\"event\":\"progress\",\"done\":1024,\"total\":4096,\"name\":\"张三 的 iPhone\"}";
        Check(ref failures, SendHelpers.JsonGetString(json, "event") == "progress", "JsonGetString event");
        Check(ref failures, SendHelpers.JsonGetString(json, "name") == "张三 的 iPhone", "JsonGetString unicode");
        Check(ref failures, SendHelpers.JsonGetLong(json, "done", -1) == 1024, "JsonGetLong done");

        string tmpDir = System.IO.Path.Combine(System.IO.Path.GetTempPath(), "db-send-test-" + Guid.NewGuid().ToString("N"));
        string d1 = System.IO.Path.Combine(tmpDir, "a");
        string d2 = System.IO.Path.Combine(tmpDir, "b");
        System.IO.Directory.CreateDirectory(d1);
        System.IO.Directory.CreateDirectory(d2);
        string f1 = System.IO.Path.Combine(d1, "photo.jpg");
        string f2 = System.IO.Path.Combine(d2, "photo.jpg");
        System.IO.File.WriteAllText(f1, "1");
        System.IO.File.WriteAllText(f2, "2");
        try
        {
            System.Collections.Generic.List<string> valid;
            Check(ref failures, SendHelpers.ValidateSendFiles(new[] { f1 }, out valid) == null && valid.Count == 1,
                "single existing file valid");
            Check(ref failures, SendHelpers.ValidateSendFiles(new[] { f1, f2 }, out valid) != null,
                "duplicate filename rejected");
        }
        finally
        {
            try { System.IO.Directory.Delete(tmpDir, true); } catch (Exception) { }
        }

        // Q75: silent autostart & Rust UI activation URI parsing
        Check(ref failures, TrayApp.AutostartValue().EndsWith(" --silent"),
            "autostart command includes --silent");
        Check(ref failures, Activation.IsUiUri("dropbridge://ui?cmd=rename&name=My%20PC"),
            "IsUiUri detects ui protocol");
        Check(ref failures, Activation.QueryParam("dropbridge://ui?cmd=rename&name=My%20PC", "name") == "My PC",
            "QueryParam decodes name");

        // Contacts verification hashing
        string h1 = TrayApp.NormalizeAndHashContact("13800138000");
        string h2 = TrayApp.NormalizeAndHashContact("+86 138-0013-8000");
        Check(ref failures, h1 != null && h1.Length == 64 && h1 == h2,
            "mobile number normalization and SHA-256 match");
        string h3 = TrayApp.NormalizeAndHashContact("  Test@Example.COM ");
        string h4 = TrayApp.NormalizeAndHashContact("test@example.com");
        Check(ref failures, h3 != null && h3.Length == 64 && h3 == h4,
            "email normalization and SHA-256 match");

        Console.WriteLine(failures == 0 ? "PASS radio_binding" : "FAIL radio_binding (" + failures + " case(s))");
        return failures == 0 ? 0 : 1;
    }

    private static void Check(ref int failures, bool ok, string what)
    {
        if (ok) return;
        failures++;
        Console.Error.WriteLine("FAIL: " + what);
    }
}
