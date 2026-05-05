import { useParams } from "react-router-dom";

import PlaybackWorkspace from "../components/PlaybackWorkspace";

/** 与首页相同的播放体验，便于从外链 `/watch/:videoId` 进入。 */
export default function WatchPage() {
  const { videoId } = useParams<{ videoId: string }>();
  if (!videoId) return null;
  return (
    <div className="flex flex-col min-h-0 overflow-hidden max-w-[1600px] mx-auto w-full px-4 sm:px-6 py-4 h-[calc(100dvh-10.5rem)] max-h-[calc(100dvh-10.5rem)]">
      <PlaybackWorkspace videoId={videoId} />
    </div>
  );
}
