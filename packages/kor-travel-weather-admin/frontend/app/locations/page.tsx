import { AdminShell } from "@/components/admin-shell";
import { LocationAdmin } from "@/components/location-admin";

export default function LocationsPage() {
  return (
    <AdminShell
      description="날씨를 조회할 위치를 등록하고 공개 여부를 관리합니다."
      section="날씨 탐색"
      title="위치 카탈로그"
    >
      <LocationAdmin />
    </AdminShell>
  );
}
