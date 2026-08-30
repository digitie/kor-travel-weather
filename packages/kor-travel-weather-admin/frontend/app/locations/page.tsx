import { LocationAdmin } from "@/components/location-admin";

export default function LocationsPage() {
  return (
    <>
      <header className="header"><div><div className="eyebrow">catalog</div><h1>위치 카탈로그</h1><p className="description">KMA 격자 anchor를 등록하고 공개 여부를 관리합니다.</p></div></header>
      <LocationAdmin />
    </>
  );
}
