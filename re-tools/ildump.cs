// Minimal reflection-only IL disassembler: ildump.exe <assembly> <outfile> [probe dirs...]
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Reflection.Emit;
using System.Text;

class ILDump
{
    static Dictionary<short, OpCode> ops = new Dictionary<short, OpCode>();
    static string[] probe;

    static void Main(string[] args)
    {
        foreach (var f in typeof(OpCodes).GetFields(BindingFlags.Public | BindingFlags.Static))
        {
            var op = (OpCode)f.GetValue(null);
            ops[op.Value] = op;
        }
        probe = args.Skip(2).ToArray();
        AppDomain.CurrentDomain.ReflectionOnlyAssemblyResolve += (s, e) =>
        {
            var n = new AssemblyName(e.Name).Name;
            foreach (var d in probe.Concat(new[] { Path.GetDirectoryName(Path.GetFullPath(args[0])) }))
                foreach (var ext in new[] { ".dll", ".exe", ".winmd" })
                {
                    var p = Path.Combine(d, n + ext);
                    if (File.Exists(p)) return Assembly.ReflectionOnlyLoadFrom(p);
                }
            try { return Assembly.ReflectionOnlyLoad(e.Name); } catch { }
            try { return Assembly.ReflectionOnlyLoad(n); } catch { }
            return null;
        };
        var asm = Assembly.ReflectionOnlyLoadFrom(args[0]);
        Type[] types;
        try { types = asm.GetTypes(); }
        catch (ReflectionTypeLoadException ex) { types = ex.Types.Where(t => t != null).ToArray(); }

        using (var w = new StreamWriter(args[1], false, Encoding.UTF8))
        {
            foreach (var t in types.OrderBy(t => t.FullName))
            {
                try { DumpType(w, t); } catch (Exception ex) { w.WriteLine("// type error " + t.FullName + ": " + ex.Message); }
            }
        }
    }

    static void DumpType(StreamWriter w, Type t)
    {
        const BindingFlags all = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance | BindingFlags.Static | BindingFlags.DeclaredOnly;
        string baseName = "";
        try { baseName = t.BaseType != null ? " : " + t.BaseType.FullName : ""; } catch { }
        w.WriteLine();
        w.WriteLine((t.IsEnum ? "enum " : t.IsInterface ? "interface " : "class ") + t.FullName + baseName);
        foreach (var f in t.GetFields(all))
        {
            string val = "";
            try { if (f.IsLiteral) val = " = " + f.GetRawConstantValue(); } catch { }
            string ft = "?"; try { ft = f.FieldType.Name; } catch { }
            w.WriteLine("  field " + ft + " " + f.Name + val);
        }
        if (t.IsEnum) return;
        foreach (var m in t.GetMethods(all).Cast<MethodBase>().Concat(t.GetConstructors(all)))
        {
            string sig;
            try { sig = m.Name + "(" + string.Join(", ", m.GetParameters().Select(p => SafeTypeName(p) + " " + p.Name)) + ")"; }
            catch { sig = m.Name + "(?)"; }
            w.WriteLine("  method " + sig);
            MethodBody body = null;
            try { body = m.GetMethodBody(); } catch { }
            if (body == null) continue;
            byte[] il = body.GetILAsByteArray();
            try { Disasm(w, m, il); } catch (Exception ex) { w.WriteLine("    // disasm error: " + ex.Message); }
        }
    }

    static string SafeTypeName(ParameterInfo p) { try { return p.ParameterType.Name; } catch { return "?"; } }

    static void Disasm(StreamWriter w, MethodBase m, byte[] il)
    {
        var mod = m.Module;
        Type[] targs = null, margs = null;
        try { targs = m.DeclaringType.IsGenericType ? m.DeclaringType.GetGenericArguments() : null; } catch { }
        try { margs = m.IsGenericMethod ? m.GetGenericArguments() : null; } catch { }
        int i = 0;
        while (i < il.Length)
        {
            int start = i;
            short code = il[i++];
            if (code == 0xFE) code = (short)(0xFE00 | il[i++]);
            OpCode op;
            if (!ops.TryGetValue(code, out op)) { w.WriteLine("    IL_{0:X4}: ??? {1:X}", start, code); continue; }
            string arg = "";
            switch (op.OperandType)
            {
                case OperandType.InlineNone: break;
                case OperandType.ShortInlineBrTarget: arg = "IL_" + (i + 1 + (sbyte)il[i]).ToString("X4"); i += 1; break;
                case OperandType.ShortInlineI: arg = ((sbyte)il[i]).ToString(); i += 1; break;
                case OperandType.ShortInlineVar: arg = il[i].ToString(); i += 1; break;
                case OperandType.InlineVar: arg = BitConverter.ToUInt16(il, i).ToString(); i += 2; break;
                case OperandType.InlineBrTarget: arg = "IL_" + (i + 4 + BitConverter.ToInt32(il, i)).ToString("X4"); i += 4; break;
                case OperandType.InlineI: { int v = BitConverter.ToInt32(il, i); arg = v + " (0x" + v.ToString("X") + ")"; i += 4; break; }
                case OperandType.InlineI8: { long v = BitConverter.ToInt64(il, i); arg = v + " (0x" + v.ToString("X") + ")"; i += 8; break; }
                case OperandType.ShortInlineR: arg = BitConverter.ToSingle(il, i).ToString(); i += 4; break;
                case OperandType.InlineR: arg = BitConverter.ToDouble(il, i).ToString(); i += 8; break;
                case OperandType.InlineString:
                    {
                        int tok = BitConverter.ToInt32(il, i); i += 4;
                        try { arg = "\"" + mod.ResolveString(tok).Replace("\n", "\\n").Replace("\r", "\\r") + "\""; } catch { arg = "str?" + tok.ToString("X"); }
                        break;
                    }
                case OperandType.InlineSwitch:
                    {
                        int n = BitConverter.ToInt32(il, i); i += 4;
                        var targets = new List<string>();
                        int baseOff = i + 4 * n;
                        for (int k = 0; k < n; k++) { targets.Add("IL_" + (baseOff + BitConverter.ToInt32(il, i)).ToString("X4")); i += 4; }
                        arg = "(" + string.Join(", ", targets) + ")";
                        break;
                    }
                case OperandType.InlineMethod:
                case OperandType.InlineField:
                case OperandType.InlineType:
                case OperandType.InlineTok:
                case OperandType.InlineSig:
                    {
                        int tok = BitConverter.ToInt32(il, i); i += 4;
                        arg = ResolveTok(mod, tok, op.OperandType, targs, margs);
                        break;
                    }
                default: arg = "?operand"; break;
            }
            w.WriteLine("    IL_{0:X4}: {1} {2}", start, op.Name, arg);
        }
    }

    static string ResolveTok(Module mod, int tok, OperandType ot, Type[] targs, Type[] margs)
    {
        try
        {
            if (ot == OperandType.InlineSig) return "sig:" + tok.ToString("X");
            var mem = mod.ResolveMember(tok, targs, margs);
            string dt = "";
            try { dt = mem.DeclaringType != null ? mem.DeclaringType.FullName + "::" : ""; } catch { }
            if (mem is Type) return ((Type)mem).FullName;
            return dt + mem.Name;
        }
        catch (Exception) { return "tok:" + tok.ToString("X8"); }
    }
}
