import { useEffect, useState, useRef } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { Header } from '@/components/Header';
import { ResultsSection } from '@/components/ResultsSection';
import { Button } from '@/components/ui/button';
import { useToast } from '@/hooks/use-toast';
import { ArrowLeft, Download, Share2, Upload, Play, Volume2 } from 'lucide-react';
import type { AnalysisResults } from '@/types/analysis';

const ResultsPage = () => {
  const location = useLocation();
  const navigate = useNavigate();
  const { toast } = useToast();
  const [playbackUrl, setPlaybackUrl] = useState<string | null>(null);
  const mediaRef = useRef<HTMLVideoElement | HTMLAudioElement>(null);

  const results: AnalysisResults | null = location.state?.results || null;

  useEffect(() => {
    if (!results) {
      navigate('/');
    }
  }, [results, navigate]);

  useEffect(() => {
    const isVideo = results?.gcs_path?.match(/\.(mp4|webm|mov|avi|mkv)$/i);
    const filePath = isVideo ? results?.gcs_path : results?.audio_gcs_path || results?.gcs_path;
    if (!filePath) return;
    const API_BASE = import.meta.env.VITE_API_URL || '/api';
    fetch(`${API_BASE}/playback-url`, {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({ gcs_path: filePath }).toString(),
    })
      .then(r => r.json())
      .then(d => setPlaybackUrl(d.url))
      .catch(() => {});
  }, [results]);

  const handleNewUpload = () => {
    navigate('/');
  };

  const fmtTime = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`;

  const priorityColor = (p: string) =>
    ({ high: '#dc2626', medium: '#d97706', low: '#16a34a' })[p?.toLowerCase()] ?? '#6b7280';

  const esc = (s: string) =>
    s?.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;') ?? '';

  const handleExport = () => {
    if (!results) return;

    const doc = `<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <title>Meeting Analysis Report</title>
  <style>
    *{box-sizing:border-box;margin:0;padding:0}
    body{font-family:'Segoe UI',Arial,sans-serif;color:#1a1a1a;font-size:13px;line-height:1.6;padding:40px 50px}
    h1{font-size:26px;font-weight:700;margin-bottom:6px}
    .meta{display:flex;justify-content:space-between;color:#888;font-size:11px;padding-bottom:12px;border-bottom:1px solid #ddd;margin-bottom:28px}
    .sh{background:#1e1e1e;color:#fff;padding:9px 14px;border-radius:4px;font-weight:700;font-size:12px;letter-spacing:.8px;margin-bottom:14px;-webkit-print-color-adjust:exact;print-color-adjust:exact}
    .sh span{font-weight:400;font-size:11px}
    .summary{color:#333;margin-bottom:28px}
    .ai{margin-bottom:14px;padding-left:18px}
    .ai-meta{font-size:11px;color:#888}
    .kd{margin-bottom:18px;padding-left:18px}
    .kd-num{color:#888;font-size:11px;margin-bottom:3px}
    .kd-title{font-weight:700;color:#111;margin-bottom:5px}
    .kd-detail{font-size:12px;color:#555;margin-bottom:2px}
    .seg{margin-bottom:10px}
    .spk{font-weight:700;font-size:12px;color:#3535bb;margin-bottom:2px}
    .footer{margin-top:40px;border-top:1px solid #ddd;padding-top:10px;text-align:center;font-size:10px;color:#aaa}
    @media print{
      body{padding:0}
      @page{margin:15mm}
      .sh{-webkit-print-color-adjust:exact;print-color-adjust:exact}
    }
  </style>
</head>
<body>
  <h1>Meeting Analysis Report</h1>
  <div class="meta">
    <span>Generated: ${new Date().toLocaleString()}</span>
    <span>Processing time: ${results.processing_time?.toFixed(1)}s</span>
  </div>

  <div class="sh">SUMMARY</div>
  <p class="summary">${esc(results.summary || 'No summary available.')}</p>

  <div class="sh">ACTION ITEMS <span>(${results.action_items?.length || 0} total)</span></div>
  ${(results.action_items || []).map((item, i) => `
  <div class="ai">
    <div><b>${i + 1}.</b> <span style="color:${priorityColor(item.priority)};font-size:10px;font-weight:700;margin-left:4px">${esc(item.priority?.toUpperCase() || '')}</span></div>
    <div style="color:#222;margin-bottom:3px">${esc(item.text)}</div>
    <div class="ai-meta">${item.assignee ? `Assignee: ${esc(item.assignee)}` : ''}${item.assignee && item.deadline ? ' &nbsp;•&nbsp; ' : ''}${item.deadline ? `Deadline: ${esc(item.deadline)}` : ''}</div>
  </div>`).join('')}

  <div class="sh" style="margin-top:24px">KEY DECISIONS <span>(${results.key_decisions?.length || 0} total)</span></div>
  ${(results.key_decisions || []).map((kd, i) => `
  <div class="kd">
    <div class="kd-num">${i + 1}.</div>
    <div class="kd-title">${esc(kd.decision)}</div>
    ${kd.rationale ? `<div class="kd-detail"><b>Rationale:</b> ${esc(kd.rationale)}</div>` : ''}
    ${kd.impact ? `<div class="kd-detail"><b>Impact:</b> ${esc(kd.impact)}</div>` : ''}
  </div>`).join('')}

  <div class="sh" style="margin-top:24px">TRANSCRIPT <span>(${results.transcript?.length || 0} segments)</span></div>
  ${(results.transcript || []).map(seg => `
  <div class="seg">
    <div class="spk">${esc(seg.speaker)}&nbsp;&nbsp;[${fmtTime(seg.start_time)} – ${fmtTime(seg.end_time)}]</div>
    <div style="color:#333">${esc(seg.text)}</div>
  </div>`).join('')}

  <div class="footer">Manthan &nbsp;•&nbsp; ${new Date().toLocaleDateString()}</div>
  <script>window.onload=function(){setTimeout(function(){window.print()},300)}<\/script>
</body>
</html>`;

    const win = window.open('', '_blank');
    if (!win) {
      toast({ variant: 'destructive', title: 'Popup blocked', description: 'Allow popups for this site to export PDF' });
      return;
    }
    win.document.write(doc);
    win.document.close();

    toast({ title: 'Export ready', description: 'Choose "Save as PDF" in the print dialog' });
  };

  const handleShare = () => {
    if (!results) return;

    const shareText = [
      `Meeting Summary:`,
      ``,
      results.summary,
      ``,
      `Action Items:`,
      ...(results.action_items || []).map((item: { text: string }) => `• ${item.text}`),
      ``,
      `Key Decisions:`,
      ...(results.key_decisions || []).map((kd: { decision: string }) => `• ${kd.decision}`),
    ].join('\n');

    if (navigator.share) {
      navigator.share({ title: 'Meeting Analysis', text: shareText });
    } else {
      navigator.clipboard.writeText(shareText);
      toast({
        title: "Copied",
        description: "Summary copied to clipboard",
      });
    }
  };

  if (!results) {
    return null;
  }

  return (
    <div className="min-h-screen">
      <Header processingTime={results.processing_time} />

      <main className="px-6 lg:px-10 xl:px-16 py-8">
        {/* Page header */}
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 mb-8 animate-fade-in">
          <div>
            <button
              onClick={handleNewUpload}
              className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors mb-2"
            >
              <ArrowLeft className="w-3.5 h-3.5" />
              New upload
            </button>
            <h1 className="text-2xl font-bold tracking-tight">Analysis results</h1>
          </div>

          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={handleExport}
              className="h-9"
            >
              <Download className="w-3.5 h-3.5 mr-1.5" />
              Export
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={handleShare}
              className="h-9"
            >
              <Share2 className="w-3.5 h-3.5 mr-1.5" />
              Share
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={handleNewUpload}
              className="h-9"
            >
              <Upload className="w-3.5 h-3.5 mr-1.5" />
              New
            </Button>
          </div>
        </div>

        {playbackUrl && (
          <div className="surface-raised rounded-xl border border-border/30 p-4 mb-8 animate-fade-in">
            {results?.gcs_path?.match(/\.(mp4|webm|mov|avi|mkv)$/i) ? (
              <video ref={mediaRef as React.RefObject<HTMLVideoElement>} controls className="w-full rounded-lg max-h-[400px]" src={playbackUrl}>
                Your browser does not support video playback.
              </video>
            ) : (
              <div className="flex items-center gap-3">
                <div className="w-10 h-10 rounded-full bg-primary/10 flex items-center justify-center">
                  <Volume2 className="w-5 h-5 text-primary" />
                </div>
                <div className="flex-1">
                  <p className="text-sm font-medium mb-1">{results?.filename || 'Recording'}</p>
                  <audio ref={mediaRef as React.RefObject<HTMLAudioElement>} controls className="w-full h-8" src={playbackUrl}>
                    Your browser does not support audio playback.
                  </audio>
                </div>
              </div>
            )}
          </div>
        )}

        <ResultsSection results={results} mediaRef={mediaRef} />
      </main>

      {/* Background */}
      <div className="fixed inset-0 -z-10 overflow-hidden pointer-events-none">
        <div className="absolute top-0 right-0 w-[600px] h-[600px] rounded-full bg-primary/3 blur-[120px]" />
        <div className="absolute bottom-0 left-1/4 w-[500px] h-[500px] rounded-full bg-primary/4 blur-[100px]" />
      </div>
    </div>
  );
};

export default ResultsPage;
