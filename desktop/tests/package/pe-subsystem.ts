/**
 * PE header subsystem reader — deterministic "no console window" check.
 *
 * A Windows PE's Subsystem field (Optional Header, offset 68 for both PE32 and
 * PE32+) is 2 for IMAGE_SUBSYSTEM_WINDOWS_GUI (no console) and 3 for
 * IMAGE_SUBSYSTEM_WINDOWS_CUI (console). Used by the package audits to prove the
 * packaged executables are GUI-subsystem binaries that never pop a console.
 */
import fs from "node:fs";

export type PeSubsystem = "gui" | "console" | "unknown";

export function peSubsystem(exePath: string): PeSubsystem {
  let buf: Buffer;
  try {
    buf = fs.readFileSync(exePath);
  } catch {
    return "unknown";
  }
  if (buf.length < 0x40) return "unknown";
  const peOffset = buf.readUInt32LE(0x3c);
  if (peOffset + 24 + 70 > buf.length) return "unknown";
  if (buf.readUInt32LE(peOffset) !== 0x00004550) return "unknown"; // "PE\0\0"
  const optionalStart = peOffset + 24;
  const magic = buf.readUInt16LE(optionalStart);
  if (magic !== 0x20b && magic !== 0x10b) return "unknown"; // PE32+ / PE32
  const subsystem = buf.readUInt16LE(optionalStart + 68);
  if (subsystem === 2) return "gui";
  if (subsystem === 3) return "console";
  return "unknown";
}
