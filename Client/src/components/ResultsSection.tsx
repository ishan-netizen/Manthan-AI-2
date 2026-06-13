import { useState, useEffect, useCallback } from 'react';
import {
  FileText,
  BarChart3,
  CheckSquare,
  Target,
  Copy,
  Download,
  Search,
  Clock,
  Hash,
  CheckCircle2,
  Languages,
  Loader2,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Badge } from '@/components/ui/badge';
import { useToast } from '@/hooks/use-toast';
import { translateText } from '@/lib/api/analysis';
import type { AnalysisResults } from '@/types/analysis';

interface ResultsSectionProps {
  results: AnalysisResults;
  mediaRef?: React.RefObject<HTMLVideoElement | HTMLAudioElement>;
}

const statItems = [
  { icon: Clock, label: 'Processing time', getValue: (r: AnalysisResults) => `${r.processing_time?.toFixed(1) || '—'}s` },
  { icon: Hash, label: 'Word count', getValue: (r: AnalysisResults) => {
    const text = r.transcript?.map(s => s.text).join(' ') || '';
    return text.split(' ').filter(w => w.length > 0).length.toLocaleString();
  }},
  { icon: FileText, label: 'Est. duration', getValue: (r: AnalysisResults) => {
    if (r.duration) return `${Math.ceil(r.duration / 60)} min`;
    const text = r.transcript?.map(s => s.text).join(' ') || '';
    const count = text.split(' ').filter(w => w.length > 0).length;
    return `${Math.max(1, Math.ceil(count / 150))} min`;
  }},
  { icon: CheckCircle2, label: 'Action items', getValue: (r: AnalysisResults) => `${r.action_items?.length || 0}` },
];

const tabs = [
  { id: 'transcript', icon: FileText, label: 'Transcript' },
  { id: 'summary', icon: BarChart3, label: 'Summary' },
  { id: 'actions', icon: CheckSquare, label: 'Actions' },
  { id: 'decisions', icon: Target, label: 'Decisions' },
];

const priorityColors: Record<number, string> = {
  0: 'border-red-500/40 text-red-400 bg-red-500/5',
  1: 'border-amber-500/40 text-amber-400 bg-amber-500/5',
  2: 'border-emerald-500/40 text-emerald-400 bg-emerald-500/5',
};

const priorityLabels: Record<number, string> = {
  0: 'High',
  1: 'Medium',
  2: 'Low',
};

export const ResultsSection = ({ results, mediaRef }: ResultsSectionProps) => {
  const [searchTerm, setSearchTerm] = useState('');
  const [actionItems, setActionItems] = useState(results.action_items?.map(item => item.text) || []);
  const [completedItems, setCompletedItems] = useState<Set<number>>(new Set());
  const [newActionItem, setNewActionItem] = useState('');
  const [translations, setTranslations] = useState<Record<string, { loading: boolean; text?: string; lang?: string }>>({});
  const [activeSegment, setActiveSegment] = useState<string | null>(null);
  const { toast } = useToast();

  const seekTo = useCallback((seconds: number) => {
    if (mediaRef?.current) {
      mediaRef.current.currentTime = seconds;
      mediaRef.current.play();
    }
  }, [mediaRef]);

  useEffect(() => {
    const media = mediaRef?.current;
    if (!media) return;
    const onTimeUpdate = () => {
      const t = media.currentTime;
      const seg = results.transcript?.find(s => t >= s.start_time && t <= s.end_time);
      setActiveSegment(seg?.id || null);
    };
    media.addEventListener('timeupdate', onTimeUpdate);
    return () => media.removeEventListener('timeupdate', onTimeUpdate);
  }, [mediaRef, results.transcript]);

  const handleTranslate = async (segId: string, text: string, lang: 'hi' | 'en') => {
    const key = `${segId}:${lang}`
    if (translations[key]?.text) {
      setTranslations(prev => {
        const next = { ...prev }
        delete next[key]
        return next
      })
      return
    }
    setTranslations(prev => ({ ...prev, [key]: { loading: true } }))
    try {
      const translated = await translateText(text, lang)
      setTranslations(prev => ({ ...prev, [key]: { loading: false, text: translated, lang } }))
    } catch {
      setTranslations(prev => {
        const next = { ...prev }
        delete next[key]
        return next
      })
      toast({ title: 'Translation failed', variant: 'destructive' })
    }
  }

  const copyToClipboard = async (text: string, label: string) => {
    try {
      await navigator.clipboard.writeText(text);
      toast({ title: "Copied", description: `${label} copied to clipboard` });
    } catch {
      toast({ title: "Failed", description: "Could not copy to clipboard", variant: "destructive" });
    }
  };

  const highlightText = (text: string, search: string) => {
    if (!search || search.length < 2) return text;
    const regex = new RegExp(`(${search.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})`, 'gi');
    return text.replace(regex, '<mark class="bg-primary/20 text-primary rounded-sm px-0.5">$1</mark>');
  };

  const addActionItem = () => {
    if (newActionItem.trim()) {
      setActionItems([...actionItems, newActionItem.trim()]);
      setNewActionItem('');
    }
  };

  const removeActionItem = (index: number) => {
    setActionItems(actionItems.filter((_, i) => i !== index));
    setCompletedItems(prev => {
      const next = new Set(prev);
      next.delete(index);
      return next;
    });
  };

  const toggleCompleted = (index: number) => {
    setCompletedItems(prev => {
      const next = new Set(prev);
      if (next.has(index)) next.delete(index);
      else next.add(index);
      return next;
    });
  };

  const transcriptionText = results.transcript?.map(segment => segment.text).join(' ') || '';

  return (
    <div className="space-y-8 animate-fade-in-up">
      {/* Quick Stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {statItems.map((stat) => (
          <div key={stat.label} className="surface-raised rounded-lg p-4">
            <div className="flex items-center gap-2 mb-1.5">
              <stat.icon className="w-4 h-4 text-primary/70" />
              <span className="text-xs text-muted-foreground">{stat.label}</span>
            </div>
            <p className="text-lg font-semibold tabular-nums">{stat.getValue(results)}</p>
          </div>
        ))}
      </div>

      <Tabs defaultValue="transcript" className="space-y-6">
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
          <TabsList className="surface-raised h-10 p-1 gap-0 inline-flex">
            {tabs.map((tab) => (
              <TabsTrigger
                key={tab.id}
                value={tab.id}
                className="h-8 px-3.5 text-sm gap-1.5 data-[state=active]:bg-primary/15 data-[state=active]:text-primary data-[state=active]:shadow-none rounded-md"
              >
                <tab.icon className="w-4 h-4" />
                <span className="hidden sm:inline">{tab.label}</span>
              </TabsTrigger>
            ))}
          </TabsList>

          <div className="relative w-full sm:w-64">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
            <Input
              placeholder="Search transcript..."
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              className="pl-9 h-9 text-sm surface-raised"
            />
          </div>
        </div>

        {/* Transcript tab */}
        <TabsContent value="transcript">
          <div className="surface-raised rounded-xl p-6">
            <div className="flex items-center justify-between mb-5">
              <h3 className="font-semibold">Full transcript</h3>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => copyToClipboard(transcriptionText, 'Transcript')}
                className="h-8 text-xs"
              >
                <Copy className="w-3.5 h-3.5 mr-1.5" />
                Copy all
              </Button>
            </div>

            {results.transcript && results.transcript.length > 0 ? (
              <div className="space-y-3 max-h-[500px] overflow-y-auto pr-2">
                {results.transcript.map((segment, index) => (
                  <div key={segment.id || index} className={`flex gap-3 group ${activeSegment === segment.id ? 'bg-primary/5 rounded-lg -mx-2 px-2' : ''}`} ref={activeSegment === segment.id ? (el) => { el?.scrollIntoView({ behavior: 'smooth', block: 'center' }); } : undefined}>
                    <div className="flex-shrink-0 w-16 text-right">
                      <span
                        className="text-xs text-muted-foreground tabular-nums cursor-pointer hover:text-primary transition-colors"
                        onClick={() => seekTo(segment.start_time)}
                      >
                        {String(Math.floor(segment.start_time / 60)).padStart(2, '0')}:{String(Math.floor(segment.start_time % 60)).padStart(2, '0')}
                      </span>
                    </div>
                    <div className="flex-1 min-w-0">
                      <span className="text-xs font-medium text-primary/80 mr-2">{segment.speaker}</span>
                      <span
                        className="text-sm leading-relaxed"
                        dangerouslySetInnerHTML={{
                          __html: highlightText(segment.text, searchTerm)
                        }}
                      />
                      <div className="flex items-center gap-1 mt-1 opacity-0 group-hover:opacity-100 transition-opacity">
                        <button
                          onClick={() => handleTranslate(segment.id || String(index), segment.text, 'hi')}
                          className="inline-flex items-center gap-0.5 text-[10px] text-muted-foreground hover:text-primary"
                        >
                          {translations[`${segment.id || index}:hi`]?.loading ? (
                            <Loader2 className="w-3 h-3 animate-spin" />
                          ) : (
                            <Languages className="w-3 h-3" />
                          )}
                          {translations[`${segment.id || index}:hi`]?.text ? 'Hide Hindi' : 'हिंदी'}
                        </button>
                        <span className="text-muted-foreground/40">·</span>
                        <button
                          onClick={() => handleTranslate(segment.id || String(index), segment.text, 'en')}
                          className="inline-flex items-center gap-0.5 text-[10px] text-muted-foreground hover:text-primary"
                        >
                          {translations[`${segment.id || index}:en`]?.loading ? (
                            <Loader2 className="w-3 h-3 animate-spin" />
                          ) : (
                            <Languages className="w-3 h-3" />
                          )}
                          {translations[`${segment.id || index}:en`]?.text ? 'Hide English' : 'English'}
                        </button>
                      </div>
                      {(translations[`${segment.id || index}:hi`]?.text || translations[`${segment.id || index}:en`]?.text) && (
                        <div className="mt-1.5 space-y-1">
                          {translations[`${segment.id || index}:hi`]?.text && (
                            <p className="text-xs text-primary/70 italic pl-2 border-l-2 border-primary/20">
                              {translations[`${segment.id || index}:hi`]!.text}
                            </p>
                          )}
                          {translations[`${segment.id || index}:en`]?.text && (
                            <p className="text-xs text-primary/70 italic pl-2 border-l-2 border-primary/20">
                              {translations[`${segment.id || index}:en`]!.text}
                            </p>
                          )}
                        </div>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-sm text-muted-foreground py-8 text-center">No transcript available</p>
            )}
          </div>
        </TabsContent>

        {/* Summary tab */}
        <TabsContent value="summary">
          <div className="surface-raised rounded-xl p-6">
            <div className="flex items-center justify-between mb-5">
              <h3 className="font-semibold">AI-generated summary</h3>
              <div className="flex items-center gap-2">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => copyToClipboard(results.summary || '', 'Summary')}
                  className="h-8 text-xs"
                >
                  <Copy className="w-3.5 h-3.5 mr-1.5" />
                  Copy
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-8 text-xs"
                >
                  <Download className="w-3.5 h-3.5 mr-1.5" />
                  Export PDF
                </Button>
              </div>
            </div>
            {results.summary ? (
              <div className="prose prose-sm prose-invert max-w-none text-muted-foreground leading-relaxed">
                {results.summary}
              </div>
            ) : (
              <p className="text-sm text-muted-foreground py-8 text-center">No summary available</p>
            )}
          </div>
        </TabsContent>

        {/* Actions tab */}
        <TabsContent value="actions">
          <div className="surface-raised rounded-xl p-6">
            <div className="flex items-center justify-between mb-5">
              <h3 className="font-semibold">Action items</h3>
              <Badge variant="secondary" className="text-xs">
                {actionItems.length - completedItems.size} of {actionItems.length} remaining
              </Badge>
            </div>

            <div className="space-y-1 mb-4">
              {actionItems.length > 0 ? (
                actionItems.map((item, index) => (
                  <div
                    key={index}
                    className={`flex items-center gap-3 px-3 py-2.5 rounded-lg transition-colors group ${
                      completedItems.has(index) ? 'opacity-50' : ''
                    }`}
                  >
                    <button
                      onClick={() => toggleCompleted(index)}
                      className={`flex-shrink-0 w-5 h-5 rounded-full border-2 flex items-center justify-center transition-colors ${
                        completedItems.has(index)
                          ? 'border-primary bg-primary'
                          : 'border-muted-foreground/30 hover:border-primary/50'
                      }`}
                    >
                      {completedItems.has(index) && (
                        <CheckCircle2 className="w-3.5 h-3.5 text-primary-foreground" />
                      )}
                    </button>
                    <span className={`flex-1 text-sm ${completedItems.has(index) ? 'line-through' : ''}`}>
                      {item}
                    </span>
                    <button
                      onClick={() => removeActionItem(index)}
                      className="opacity-0 group-hover:opacity-100 text-muted-foreground hover:text-destructive transition-all text-sm px-1"
                    >
                      ×
                    </button>
                  </div>
                ))
              ) : (
                <p className="text-sm text-muted-foreground py-4 text-center">No action items yet</p>
              )}
            </div>

            <div className="flex gap-2">
              <Input
                placeholder="Add an action item..."
                value={newActionItem}
                onChange={(e) => setNewActionItem(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && addActionItem()}
                className="h-9 text-sm surface-raised"
              />
              <Button
                onClick={addActionItem}
                size="sm"
                className="gradient-primary h-9"
                disabled={!newActionItem.trim()}
              >
                Add
              </Button>
            </div>
          </div>
        </TabsContent>

        {/* Decisions tab */}
        <TabsContent value="decisions">
          <div className="surface-raised rounded-xl p-6">
            <div className="flex items-center justify-between mb-5">
              <h3 className="font-semibold">Key decisions</h3>
              <Badge variant="secondary" className="text-xs">
                {results.key_decisions?.length || 0} total
              </Badge>
            </div>

            {results.key_decisions && results.key_decisions.length > 0 ? (
              <div className="space-y-3">
                {results.key_decisions.map((decision, index) => (
                  <div
                    key={decision.id || index}
                    className={`surface-raised rounded-lg p-4 border-l-[3px] ${priorityColors[index % 3]}`}
                  >
                    <div className="flex items-start justify-between gap-4">
                      <div className="flex-1 min-w-0">
                        <p className="text-sm font-medium mb-1">{decision.decision}</p>
                        {decision.rationale && (
                          <p className="text-xs text-muted-foreground mb-1.5">
                            {decision.rationale}
                          </p>
                        )}
                        {decision.impact && (
                          <p className="text-xs text-muted-foreground">
                            Impact: {decision.impact}
                          </p>
                        )}
                      </div>
                      <Badge
                        variant="outline"
                        className={`text-[10px] uppercase tracking-wide font-medium flex-shrink-0 ${priorityColors[index % 3]}`}
                      >
                        {priorityLabels[index % 3]}
                      </Badge>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-sm text-muted-foreground py-8 text-center">No key decisions found</p>
            )}
          </div>
        </TabsContent>
      </Tabs>
    </div>
  );
};
