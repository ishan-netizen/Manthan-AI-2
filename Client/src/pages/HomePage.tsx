import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '@/contexts/AuthContext';
import { Header } from '@/components/Header';
import { FileUpload } from '@/components/FileUpload';
import { FloatingActionButton } from '@/components/FloatingActionButton';
import { Button } from '@/components/ui/button';
import { useToast } from '@/hooks/use-toast';
import { LogIn, UserPlus, Brain, Zap, Shield, Clock } from 'lucide-react';
import heroBackground from '@/assets/hero-background.jpg';

interface AnalysisResults {
  transcript: Array<{
    id: string;
    speaker: string;
    text: string;
    start_time: number;
    end_time: number;
    confidence: number;
  }>;
  summary: string;
  action_items: Array<{
    id: string;
    text: string;
    assignee?: string;
    deadline?: string;
    priority: string;
    confidence: number;
  }>;
  key_decisions: Array<{
    id: string;
    decision: string;
    rationale?: string;
    impact: string;
    confidence: number;
  }>;
  processing_time: number;
}

const HomePage = () => {
  const [isProcessing, setIsProcessing] = useState(false);
  const { isAuthenticated, logout } = useAuth();
  const { toast } = useToast();
  const navigate = useNavigate();

  const handleFileAnalyzed = (analysisResults: AnalysisResults) => {
    // Navigate to results page with the analysis data
    navigate('/results', { state: { results: analysisResults } });
  };

  const handleNewUpload = () => {
    // Navigate back to home (file upload)
    navigate('/');
  };

  const handleEmailSignIn = () => {
    navigate('/login');
  };

  const handleEmailSignup = () => {
    navigate('/signup');
  };

  const handleSignOut = () => {
    logout();
    navigate('/');
    toast({
      title: "Signed out",
      description: "You've been successfully signed out",
    });
  };

  return (
    <div className="min-h-screen">
      <Header 
        isProcessing={isProcessing}
      />
      
      <main className="container mx-auto px-6 py-8">
        {!isAuthenticated ? (
          /* Landing Page */
          <div className="max-w-6xl mx-auto">
            {/* Hero Section */}
            <div className="text-center mb-16 animate-fade-in">
              <div className="mb-8">
                <h1 className="text-6xl font-bold mb-6 bg-gradient-to-r from-white via-primary-glow to-primary bg-clip-text text-transparent">
                  Manthan AI
                </h1>
                <p className="text-2xl text-muted-foreground mb-4 max-w-3xl mx-auto">
                  Transform your meetings into actionable insights with AI-powered transcription, 
                  summaries, and intelligent analysis
                </p>
                <p className="text-lg text-muted-foreground/80">
                  Join thousands of professionals who trust MeetingMind AI for smarter meetings
                </p>
              </div>
            </div>

            {/* Features Grid */}
            <div className="grid md:grid-cols-4 gap-6 mb-16">
              <div className="glass border border-glass-border/30 rounded-xl p-6 text-center hover:border-primary/30 transition-all duration-300">
                <div className="w-12 h-12 rounded-full bg-primary/20 flex items-center justify-center mx-auto mb-4">
                  <Brain className="w-6 h-6 text-primary" />
                </div>
                <h3 className="font-semibold mb-2">AI Transcription</h3>
                <p className="text-sm text-muted-foreground">Accurate speech-to-text with speaker identification</p>
              </div>
              <div className="glass border border-glass-border/30 rounded-xl p-6 text-center hover:border-primary/30 transition-all duration-300">
                <div className="w-12 h-12 rounded-full bg-primary/20 flex items-center justify-center mx-auto mb-4">
                  <Zap className="w-6 h-6 text-primary" />
                </div>
                <h3 className="font-semibold mb-2">Smart Summary</h3>
                <p className="text-sm text-muted-foreground">Key points and decisions in seconds</p>
              </div>
              <div className="glass border border-glass-border/30 rounded-xl p-6 text-center hover:border-primary/30 transition-all duration-300">
                <div className="w-12 h-12 rounded-full bg-primary/20 flex items-center justify-center mx-auto mb-4">
                  <Clock className="w-6 h-6 text-primary" />
                </div>
                <h3 className="font-semibold mb-2">Action Items</h3>
                <p className="text-sm text-muted-foreground">Automatically extract tasks and follow-ups</p>
              </div>
              <div className="glass border border-glass-border/30 rounded-xl p-6 text-center hover:border-primary/30 transition-all duration-300">
                <div className="w-12 h-12 rounded-full bg-primary/20 flex items-center justify-center mx-auto mb-4">
                  <Shield className="w-6 h-6 text-primary" />
                </div>
                <h3 className="font-semibold mb-2">Secure & Private</h3>
                <p className="text-sm text-muted-foreground">Enterprise-grade security for your data</p>
              </div>
            </div>

            {/* Sign In Section */}
            <div className="max-w-md mx-auto">
              <div className="glass border border-glass-border/30 rounded-2xl p-8 text-center">
                <h2 className="text-2xl font-bold mb-2">Get Started</h2>
                <p className="text-muted-foreground mb-8">Use email and password to access your account</p>
                
                <div className="space-y-4">
                  <Button 
                    onClick={handleEmailSignIn}
                    variant="gradient" 
                    size="lg" 
                    className="w-full"
                  >
                    <LogIn className="w-5 h-5 mr-2" />
                    Login with Email
                  </Button>

                  <Button 
                    onClick={handleEmailSignup}
                    variant="outline" 
                    size="lg" 
                    className="w-full"
                  >
                    <UserPlus className="w-5 h-5 mr-2" />
                    Sign up with Email
                  </Button>
                </div>

                <div className="mt-6 pt-6 border-t border-glass-border/30">
                  <p className="text-xs text-muted-foreground">
                    By signing in, you agree to our Terms of Service and Privacy Policy
                  </p>
                </div>
              </div>
            </div>
          </div>
        ) : (
          /* File Upload Section (Authenticated) */
          <div className="max-w-4xl mx-auto">
            <div className="text-center mb-12 animate-fade-in">
              <div 
                className="relative overflow-hidden rounded-3xl mb-8 h-64 flex items-center justify-center"
                style={{
                  backgroundImage: `url(${heroBackground})`,
                  backgroundSize: 'cover',
                  backgroundPosition: 'center'
                }}
              >
                <div className="absolute inset-0 bg-black/50 backdrop-blur-sm" />
                <div className="relative z-10 text-center">
                  <h1 className="text-5xl font-bold mb-4 bg-gradient-to-r from-white via-white to-white/80 bg-clip-text text-transparent">
                    Transform Your Meetings
                  </h1>
                  <p className="text-xl text-white/90 mb-2">
                    Upload your meeting recordings and get instant AI-powered analysis
                  </p>
                  <p className="text-sm text-white/70">
                    Transcription • Summary • Action Items • Key Decisions
                  </p>
                </div>
              </div>
            </div>
            
            <div className="max-w-2xl mx-auto">
              <FileUpload
                onFileAnalyzed={handleFileAnalyzed}
                isProcessing={isProcessing}
                setIsProcessing={setIsProcessing}
              />
            </div>

            <div className="flex justify-center mt-8">
              <Button
                onClick={handleSignOut}
                variant="outline"
                className="px-6 py-2"
              >
                Sign Out
              </Button>
            </div>
          </div>
        )}
      </main>

      {/* Floating Action Button */}
      <FloatingActionButton
        onNewUpload={handleNewUpload}
        hasResults={false}
      />

      {/* Background decorative elements */}
      <div className="fixed inset-0 -z-10 overflow-hidden">
        <div className="absolute -top-40 -right-40 w-80 h-80 rounded-full bg-primary/10 blur-3xl animate-float" />
        <div className="absolute -bottom-40 -left-40 w-80 h-80 rounded-full bg-primary-glow/10 blur-3xl animate-float" style={{ animationDelay: '2s' }} />
        <div className="absolute top-1/2 left-1/2 transform -translate-x-1/2 -translate-y-1/2 w-96 h-96 rounded-full bg-primary/5 blur-3xl animate-float" style={{ animationDelay: '4s' }} />
      </div>
    </div>
  );
};

export default HomePage;
