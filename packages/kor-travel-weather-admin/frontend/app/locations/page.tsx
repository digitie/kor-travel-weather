import { LocationAdmin } from "@/components/location-admin";
import { PageHeader } from "@/components/admin-shell";

export default function LocationsPage() {
  return (
    <>
      <PageHeader
        description="KMA 격자 anchor를 등록하고 공개 여부를 관리합니다."
        section="날씨 탐색"
        title="위치 카탈로그"
      />
      <LocationAdmin />
    </>
  );
}
