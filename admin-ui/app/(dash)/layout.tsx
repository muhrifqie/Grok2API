import { AdminShell } from "@/app/components/shell";

export default function DashLayout({ children }: { children: React.ReactNode }) {
  return <AdminShell>{children}</AdminShell>;
}
