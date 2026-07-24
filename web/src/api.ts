import axios from 'axios'
export const api=axios.create({baseURL:'/api/v1',withCredentials:true,timeout:30000})
let accessToken=''
export const setAccessToken=(token:string)=>{accessToken=token}
api.interceptors.request.use(config=>{if(accessToken)config.headers.Authorization=`Bearer ${accessToken}`;return config})
