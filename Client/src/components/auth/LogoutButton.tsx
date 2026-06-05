import { useAuth } from '@/contexts/AuthContext'
import { Button } from '@/components/ui/button'
import { LogOut } from 'lucide-react'

export const LogoutButton = () => {
  const { logout } = useAuth()

  return (
    <Button 
      onClick={logout}
      variant="outline"
      className="flex items-center gap-2"
    >
      <LogOut size={16} />
      Sign Out
    </Button>
  )
}