import { useNavigate } from 'react-router-dom'
import { Button } from '@/components/ui/button'
import { LogIn } from 'lucide-react'

export const LoginButton = () => {
  const navigate = useNavigate()

  return (
    <Button 
      onClick={() => navigate('/login')}
      className="flex items-center gap-2"
    >
      <LogIn size={16} />
      Sign In
    </Button>
  )
}